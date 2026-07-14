"""Security onboarding engine — the backend core behind "add any security".

Takes free-form user input (US symbol, Israeli security number, company name,
or a global name/ticker) and either SUGGESTS matches (partial input) or fully
RESOLVES + validates a chosen security into a row we can store. No UI here —
pure functions + a CLI (`desk/onboard_cli.py`) for validation.

Two resolvers, one engine — routed by the query:
  - US identity via the SEC ticker map (sec.gov/files/company_tickers.json).
  - Israeli (TASE) identity via MAYA search + the 2-hop companyId resolution in
    desk/maya_ids.py. Hebrew queries always route here (Yahoo 400s on Hebrew).
  - GLOBAL identity via Yahoo's public search endpoint — for non-US/non-Israel
    equities. Yahoo search is NOT a safe auto-resolver (same-ticker collisions
    return valid-but-WRONG companies with clean prices, which the NaN guard
    cannot catch — see research/GLOBAL_COVERAGE_FINDINGS.md), so global is
    resolve-assisted: suggest() surfaces candidates, the user picks the exact
    Yahoo symbol, resolve() validates it. Never auto-picked.

Price-existence for every market uses the same yfinance NaN guard as
collect_prices (closes_series) — junk tickers (e.g. SANO.TA) fall back to
price_source='manual' (US/TASE) or NotFound (global), never a guessed price.

No-guess policy: every network path is fail-soft. On any lookup failure we
return a clear NotFound / manual-fallback with a reason — never a fabricated
symbol or price. Sub-unit conversions (ILA agorot, GBp pence -> ÷100) are NOT
done here; resolve() records the post-conversion display currency and the
actual ÷100 stays in collect_prices (normalize_currency).
"""
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

import yfinance as yf
from sqlalchemy import select

from desk.collect_prices import closes_series, normalize_currency
from desk.db import get_engine, init_db, securities
from desk.maya_client import (
    COMPANY_DETAILS_URL,
    SEARCH_URL,
    gate_cleared,
    harvest_cookies,
    make_session,
)
from desk.maya_ids import resolve_company_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("onboarding")

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# SEC requires a descriptive User-Agent with contact info.
SEC_UA = "DESK watchlist onboarding (contact: yovav81@gmail.com)"
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
YAHOO_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
HEBREW_RE = re.compile(r"[֐-׿]")
US_SYMBOL_RE = re.compile(r"[A-Za-z][A-Za-z.\-]{0,5}$")
PRICE_CHECK_PERIOD = "3mo"  # wide enough to include recent IPOs (e.g. Bagira)


@dataclass
class Suggestion:
    market: str  # US | TASE | GLOBAL
    display_name: str
    symbol_or_number: str  # US ticker / TASE security number / Yahoo symbol
    hint: str


@dataclass
class ResolvedSecurity:
    sec_id: str
    symbol: str
    name: str
    market: str  # US | TASE | GLOBAL
    yahoo_symbol: str | None
    price_source: str  # yfinance | manual
    maya_company_id: int | None
    currency: str  # major/display currency (USD|ILS|EUR|GBP…); sub-unit ÷100 in collect_prices


@dataclass
class NotFound:
    reason: str


# --------------------------------------------------------------------------- #
# SEC ticker map (US)                                                          #
# --------------------------------------------------------------------------- #
_sec_cache: list[dict] | None = None


def _load_sec_tickers() -> list[dict]:
    """Fetch + cache the SEC ticker->CIK->title map. Fail-soft to []."""
    global _sec_cache
    if _sec_cache is not None:
        return _sec_cache
    try:
        req = urllib.request.Request(SEC_TICKERS_URL, headers={"User-Agent": SEC_UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = json.load(r)
        _sec_cache = [
            {"ticker": v["ticker"].upper(), "cik": v["cik_str"], "title": v["title"]}
            for v in raw.values()
        ]
        log.info("SEC ticker map: %d entries", len(_sec_cache))
    except Exception as e:
        log.warning("SEC ticker map fetch failed (%s) — US lookups degraded", e)
        _sec_cache = []
    return _sec_cache


def _sec_lookup_exact(symbol: str) -> dict | None:
    sym = symbol.upper()
    for e in _load_sec_tickers():
        if e["ticker"] == sym:
            return e
    return None


def _sec_suggest(query: str, limit: int = 8) -> list[Suggestion]:
    q = query.strip().upper()
    entries = _load_sec_tickers()
    exact = [e for e in entries if e["ticker"] == q]
    name_hits = [e for e in entries if q in e["title"].upper() and e not in exact]
    out = []
    for e in exact + name_hits[: limit - len(exact)]:
        out.append(Suggestion("US", e["title"], e["ticker"], f"US · SEC CIK {e['cik']}"))
    return out


# --------------------------------------------------------------------------- #
# Yahoo search (GLOBAL) — resolve-assisted, never auto-picked                  #
# --------------------------------------------------------------------------- #
def _yahoo_search(query: str, limit: int = 8, retries: int = 1) -> list[dict]:
    """Yahoo public search -> EQUITY candidates {symbol, name, exchange}.

    Filters to quoteType=='EQUITY' (drops ETFs/indices/options/currencies).
    Never called with Hebrew (Yahoo 400s on Hebrew — routed to MAYA); guarded
    anyway. Single calls with one retry — do NOT batch (Yahoo throttles bulk)."""
    if not query.strip() or HEBREW_RE.search(query):
        return []
    url = f"{YAHOO_SEARCH_URL}?q={urllib.parse.quote(query)}&quotesCount=10&newsCount=0"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": YAHOO_UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
            out = []
            for it in data.get("quotes", []):
                if it.get("quoteType") != "EQUITY":
                    continue
                sym = it.get("symbol")
                if not sym:
                    continue
                out.append({
                    "symbol": sym,
                    "name": it.get("shortname") or it.get("longname") or sym,
                    "exchange": it.get("exchDisp") or it.get("exchange") or "",
                })
                if len(out) >= limit:
                    break
            return out
        except Exception as e:
            log.info("Yahoo search q=%r attempt %d failed: %s", query, attempt, e)
            time.sleep(1.5)
    return []


def _yahoo_suggest(query: str) -> list[Suggestion]:
    return [
        Suggestion("GLOBAL", c["name"], c["symbol"], f"GLOBAL · {c['exchange']}")
        for c in _yahoo_search(query)
    ]


# --------------------------------------------------------------------------- #
# MAYA session (Israeli) — lazily harvested, cached per process               #
# --------------------------------------------------------------------------- #
_maya_session = None
_maya_session_tried = False


def _get_maya_session():
    """Harvest a MAYA session once per process; None if the gate can't be cleared."""
    global _maya_session, _maya_session_tried
    if _maya_session is not None or _maya_session_tried:
        return _maya_session
    _maya_session_tried = True
    cookies = harvest_cookies()
    if not gate_cleared(cookies):
        log.warning("MAYA gate not cleared (cookies=%d) — Israeli lookups unavailable this run", len(cookies))
        return None
    _maya_session = make_session(cookies)
    return _maya_session


def _maya_search(session, query: str) -> list[dict]:
    try:
        r = session.get(SEARCH_URL, params={"q": query, "culture": "he-IL"}, timeout=30)
    except Exception as e:
        log.warning("MAYA search q=%r failed: %s", query, e)
        return []
    if r.status_code != 200:
        log.warning("MAYA search q=%r -> HTTP %s", query, r.status_code)
        return []
    try:
        return (r.json() or {}).get("data") or []
    except Exception:
        return []


MAX_COMPANY_RESOLUTIONS = 6  # bound the extra /details calls per suggest()


def resolve_company_to_primary_stock(company_id, session=None) -> int | None:
    """Company -> its PRIMARY STOCK security number, or None.

    MAYA's `companies/<id>/details.mainSecurityId` is the exchange's own
    designated ordinary share (verified across banks, dual-listed, and
    small caps). Returns None — meaning NOT-RESOLVABLE-BY-NAME — when the
    company has no primary stock (bond-only issuer or nothing listed:
    `mainSecurityId` is null, or `isBond` is set). Never guesses a series.
    """
    session = session or _get_maya_session()
    if session is None or company_id is None:
        return None
    try:
        r = session.get(COMPANY_DETAILS_URL.format(company_id=company_id), timeout=30)
    except Exception as e:
        log.info("company %s details fetch failed: %s", company_id, e)
        return None
    if r.status_code != 200:
        return None
    try:
        d = r.json()
    except Exception:
        return None
    main = d.get("mainSecurityId")
    if main is None or d.get("isBond"):
        return None
    try:
        return int(main)
    except (TypeError, ValueError):
        return None


def _maya_suggest(query: str) -> list[Suggestion]:
    """Suggestions for an Israeli query. Direct security ('מניות') rows become
    security-number suggestions; company rows resolve to their PRIMARY STOCK.
    A company with no primary stock is surfaced as not-resolvable-by-name."""
    session = _get_maya_session()
    if session is None:
        return []
    rows = _maya_search(session, query)
    out: list[Suggestion] = []
    seen: set[str] = set()

    # 1. Direct tradeable securities — carry a security number already.
    for row in rows:
        if row.get("category") != "מניות":
            continue
        num = str(row.get("id") or "")
        if num and num not in seen:
            seen.add(num)
            out.append(Suggestion("TASE", row.get("name") or num, num, "TASE security"))

    # 2. Company hits -> primary stock. A name search resolves to the company's
    #    ordinary share only; bonds/other series need their exact number.
    company_rows = []
    seen_cids = set()
    for row in rows:
        if "/he/companies/" not in (row.get("url") or ""):
            continue
        cid = str(row.get("id") or "")
        if cid and cid not in seen_cids:
            seen_cids.add(cid)
            company_rows.append(row)

    for row in company_rows[:MAX_COMPANY_RESOLUTIONS]:
        cid = row.get("id")
        name = row.get("name") or ""
        primary = resolve_company_to_primary_stock(cid, session)
        if primary is None:
            key = f"company:{cid}"
            if key not in seen:
                seen.add(key)
                out.append(Suggestion("TASE", name, "", "company has no primary stock — enter a security number"))
            continue
        num = str(primary)
        if num in seen:
            continue
        seen.add(num)
        out.append(Suggestion("TASE", name, num, "TASE stock (company primary)"))
    return out


# --------------------------------------------------------------------------- #
# Classification + suggest                                                     #
# --------------------------------------------------------------------------- #
def _classify(query: str) -> str:
    q = query.strip()
    if HEBREW_RE.search(q):
        return "he_name"
    if q.isdigit():
        return "il_number"
    if US_SYMBOL_RE.fullmatch(q):
        return "us_symbol"
    return "us_name"


def suggest(query: str) -> list[Suggestion]:
    """Suggest matches for partial/ambiguous input. Multiple results, never
    auto-picked. Exact symbol/number matches rank first."""
    q = query.strip()
    if not q:
        return []
    kind = _classify(q)
    out: list[Suggestion] = []
    if kind in ("il_number", "he_name"):
        # Hebrew or a bare (6-9 digit) security number -> MAYA only.
        out = _maya_suggest(q)
    else:
        # Plain Latin ticker/name -> US (SEC) AND global (Yahoo), merged. US
        # first so a US-listed symbol wins the de-dupe over its GLOBAL twin.
        out = _sec_suggest(q) + _yahoo_suggest(q)

    # Rank: exact symbol/number match first, then stable order; de-dupe by the
    # bare identifier across markets (so US 'SAP' beats GLOBAL 'SAP', but the
    # distinct 'SAP.DE'/'SAP.TO' collisions all survive — user picks).
    ql = q.upper()
    seen = set()
    ranked = []
    for s in sorted(out, key=lambda x: 0 if x.symbol_or_number.upper() == ql else 1):
        # Empty identifier = not-resolvable company; dedupe those by name so
        # several distinct no-stock companies all survive.
        ident = s.symbol_or_number.upper() if s.symbol_or_number else f"~{s.market}:{s.display_name}"
        if ident in seen:
            continue
        seen.add(ident)
        ranked.append(s)
    return ranked


# --------------------------------------------------------------------------- #
# Price existence check (shared NaN guard)                                     #
# --------------------------------------------------------------------------- #
def _yfinance_has_prices(yahoo_symbol: str | None) -> bool:
    """True only if yfinance returns real, non-NaN closes for this symbol.
    Reuses collect_prices.closes_series so the junk guard is identical. One
    retry with a short pause absorbs the occasional Yahoo throttle (a false
    empty), so a genuine no-data symbol still resolves to False."""
    if not yahoo_symbol:
        return False
    for attempt in (0, 1):
        try:
            df = yf.download(
                [yahoo_symbol],
                period=PRICE_CHECK_PERIOD,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
        except Exception as e:
            log.info("price check for %s failed: %s", yahoo_symbol, e)
            df = None
        if df is not None and closes_series(df, yahoo_symbol) is not None:
            return True
        if attempt == 0:
            time.sleep(2.0)
    return False


# --------------------------------------------------------------------------- #
# Resolve                                                                      #
# --------------------------------------------------------------------------- #
def _existing_security(engine, sec_id: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(select(securities).where(securities.c.sec_id == sec_id)).first()
    return dict(row._mapping) if row else None


def _resolve_us(symbol: str) -> ResolvedSecurity | NotFound:
    sym = symbol.strip().upper()
    entry = _sec_lookup_exact(sym)
    if entry is None:
        return NotFound(f"US ticker {sym!r} not in the SEC registry")
    price_source = "yfinance" if _yfinance_has_prices(sym) else "manual"
    return ResolvedSecurity(
        sec_id=sym,
        symbol=sym,
        name=entry["title"],
        market="US",
        yahoo_symbol=sym,
        price_source=price_source,
        maya_company_id=None,
        currency="USD",
    )


def _resolve_tase(number: str, engine) -> ResolvedSecurity | NotFound:
    num = number.strip()
    if not num.isdigit():
        return NotFound(f"TASE identifier {num!r} is not a security number")

    session = _get_maya_session()
    name = None
    company_id = None
    if session is not None:
        for row in _maya_search(session, num):
            if str(row.get("id")) == num and row.get("category") == "מניות":
                name = row.get("name")
                break
        company_id = resolve_company_id(session, num)

    # Letter Yahoo ticker cannot be derived from a bare number (no free
    # number->ticker source; TASE is WAF-blocked). Use a known mapping if we
    # already have one; otherwise price falls back to manual (no guessing).
    existing = _existing_security(engine, num)
    yahoo_symbol = None
    symbol = num
    if existing:
        symbol = existing.get("symbol") or num
        yahoo_symbol = existing.get("yahoo_symbol") or (
            f"{symbol}.TA" if not symbol.endswith(".TA") else symbol
        )
        name = name or existing.get("name")
        company_id = company_id if company_id is not None else existing.get("maya_company_id")

    if name is None:
        return NotFound(f"TASE security {num!r} not found on MAYA and not already known")

    # yahoo_symbol is kept even when manual (reference); the collector's manual
    # tier ignores it, so a junk .TA never produces a price.
    price_source = "yfinance" if _yfinance_has_prices(yahoo_symbol) else "manual"

    return ResolvedSecurity(
        sec_id=num,
        symbol=symbol,
        name=name,
        market="TASE",
        yahoo_symbol=yahoo_symbol,
        price_source=price_source,
        maya_company_id=int(company_id) if company_id is not None else None,
        currency="ILS",
    )


def _resolve_global(symbol: str) -> ResolvedSecurity | NotFound:
    """Validate a user-chosen Yahoo symbol (e.g. SAP.DE). The symbol MUST come
    from a suggest() candidate the user picked — we never auto-resolve a name to
    a symbol (collision risk). No prices -> NotFound (no guess)."""
    sym = symbol.strip()
    if not sym:
        return NotFound("empty symbol")
    if not _yfinance_has_prices(sym):
        return NotFound(f"Yahoo symbol {sym!r} has no usable price data")

    # Native quote currency -> display (major) currency. The GBp (pence) ÷100,
    # like ILA agorot, is applied in collect_prices, not here — we only record
    # the post-conversion display currency.
    native = None
    try:
        native = yf.Ticker(sym).fast_info.get("currency")
    except Exception as e:
        log.info("currency lookup for %s failed: %s", sym, e)
    display_ccy, _scale = normalize_currency(native)

    # Name from the Yahoo search candidate matching this exact symbol.
    name = sym
    for c in _yahoo_search(sym):
        if c["symbol"].upper() == sym.upper():
            name = c["name"] or sym
            break

    return ResolvedSecurity(
        sec_id=sym,
        symbol=sym,
        name=name,
        market="GLOBAL",
        yahoo_symbol=sym,
        price_source="yfinance",
        maya_company_id=None,
        currency=display_ccy,
    )


def resolve(market: str, identifier: str, engine=None) -> ResolvedSecurity | NotFound:
    """Fully resolve + validate a chosen security. Never guesses: unresolvable
    identifiers return NotFound; unpriceable US/TASE return price_source='manual',
    unpriceable global returns NotFound."""
    m = market.strip().upper()
    if m == "US":
        return _resolve_us(identifier)
    if m == "TASE":
        engine = engine or get_engine()
        return _resolve_tase(identifier, engine)
    if m == "GLOBAL":
        return _resolve_global(identifier)
    return NotFound(f"unknown market {market!r} (expected US, TASE, or GLOBAL)")


# --------------------------------------------------------------------------- #
# Persist                                                                      #
# --------------------------------------------------------------------------- #
def add_to_db(resolved: ResolvedSecurity, engine=None) -> str:
    """Idempotently upsert a ResolvedSecurity into `securities`. Does NOT touch
    watchlist. Never downgrades an existing good row (e.g. yfinance->manual, or
    clobbering a set maya_company_id/name with NULL). Returns 'inserted',
    'updated', or 'unchanged'."""
    engine = engine or get_engine()
    init_db(engine)
    existing = _existing_security(engine, resolved.sec_id)

    new = {
        "sec_id": resolved.sec_id,
        "symbol": resolved.symbol,
        "name": resolved.name,
        "asset_type": "stock",
        "market": resolved.market,
        "price_source": resolved.price_source,
        "yahoo_symbol": resolved.yahoo_symbol,
        "maya_company_id": resolved.maya_company_id,
    }

    if existing is None:
        with engine.begin() as conn:
            conn.execute(securities.insert().values(**new))
        return "inserted"

    # Merge: only fill gaps / upgrade; never overwrite good with worse.
    updates = {}
    # price_source: only upgrade manual -> yfinance, never the reverse.
    if existing.get("price_source") != "yfinance" and new["price_source"] == "yfinance":
        updates["price_source"] = "yfinance"
    # fill-if-missing fields
    for col in ("name", "symbol", "yahoo_symbol", "maya_company_id"):
        if not existing.get(col) and new.get(col):
            updates[col] = new[col]

    if not updates:
        return "unchanged"
    from sqlalchemy import update as sa_update

    with engine.begin() as conn:
        conn.execute(sa_update(securities).where(securities.c.sec_id == resolved.sec_id).values(**updates))
    return "updated"


def to_dict(obj) -> dict:
    """asdict for a Resolved/NotFound/Suggestion — for CLI/JSON printing."""
    return asdict(obj)
