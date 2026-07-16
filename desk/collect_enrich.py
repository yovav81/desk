"""Enrich watchlisted TASE securities with their Yahoo ticker, via ISIN search.
Cloud collector, WRITE-only against DESK_DB_URL — closes the gap where a
UI-added TASE security (shallow insert) has no letter ticker and therefore no
automatic prices. Validated in research/TASE_ENRICHMENT_FINDINGS.md:
46/50 = 92% resolution, ZERO wrong-company (scale test, seed 20260716).

Method (shipped exactly as probed — do not "improve" it):
  1. construct the ISIN: "IL" + zfill(9)(security number) + Luhn check digit.
     An ISIN is the ISO 6166 globally unique id of the security ITSELF — the
     query cannot name-collide (the Reliance trap does not apply).
  2. one Yahoo search per security (onboarding._yahoo_search, EQUITY-filtered).
  3. THE TLV GATE (mandatory, single check — is_tlv_listing()): the hit must be
     a Tel Aviv listing with a .TA symbol. Camtek proved why: its ISIN returns
     ONLY the NASDAQ line (right company, USD prices) — accepting it would
     store a USD price on an ILS-quoted security. Gate fails -> stays manual.
  4. name match, as validated: Yahoo's name is LOGGED next to ours for human
     eyeballing. Identity comes from the ISIN + TLV gate (structural), not from
     automated Hebrew<->English fuzzy matching — per the scale-test rule.
  5. price_source flips to 'yfinance' (the value collect_prices' auto tier
     selects on — measured) ONLY when the NaN guard confirms real closes
     (onboarding._yfinance_has_prices, same helper the probe used). A resolved
     symbol with junk data keeps price_source='manual'; yahoo_symbol is still
     stored as reference (the manual tier ignores it — documented).

NEVER guesses: NO-HIT / non-TLV / no-prices all leave the row exactly as it
is (except storing a proven symbol) and log why. Zero wrong-company is the
property that makes this shippable.

HAND-ENTERED DATA IS SACRED: a security that already has manual_prices rows
(Sano 813014, Bio-Dvash 1082346) gets its symbol recorded but is NEVER flipped
off the manual tier by this collector — migrating them is a deliberate human
step, logged loudly here so it isn't forgotten.

CLI (SAFE BY DEFAULT — dry-run unless --commit, mirroring sec_ids):
    python -m desk.collect_enrich            # dry-run: resolve + report, write nothing
    python -m desk.collect_enrich --commit   # actually UPDATE securities
Idempotent: resolved rows leave the `yahoo_symbol IS NULL` filter. Unresolved
rows are retried each run (bounded by MAX_PER_RUN; see module notes at bottom).
"""
import argparse
import logging
import time

from sqlalchemy import func, select, update

from desk.db import get_engine, init_db, manual_prices, securities, watchlist
from desk.onboarding import _yahoo_search, _yfinance_has_prices

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_enrich")

# Same spacing the probe used (1.0s; gentler than collect_sec's 0.2 because
# each security may cost a search AND a yfinance price check).
REQUEST_DELAY = 1.0
# Per-run cap: a cold start with a large backlog must not fire hundreds of
# Yahoo requests in one run. 25 securities x (1 search + <=1 price check)
# <= ~50 requests/run; a 557-row worst case clears in ~23 runs (~6h on a
# 15-min cadence) instead of one hammering burst.
MAX_PER_RUN = 25

# What a Tel Aviv listing looks like in the search response. The probe observed
# exchDisp == 'Tel Aviv'; 'TLV' is Yahoo's raw exchange code, accepted for the
# case where exchDisp is absent and _yahoo_search falls back to it.
TLV_EXCHANGES = {"Tel Aviv", "TLV"}


# --- copied VERBATIM from research/yahoo_by_number_probe.py (the tested code) --
def isin_check_digit(base: str) -> str:
    """ISIN check digit (Luhn over letters-expanded digits)."""
    s = "".join(str(int(c, 36)) for c in base)
    total = 0
    for i, d in enumerate(reversed([int(c) for c in s])):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def tase_isin(num: str) -> str:
    base = "IL" + str(num).zfill(9)
    return base + isin_check_digit(base)


# Offline self-test against two KNOWN ISINs (Apple, Teva) — same guard the
# probe ran before spending requests; protects against accidental edits.
assert isin_check_digit("US037833100") == "5"
assert isin_check_digit("IL000629014") == "7"
# -------------------------------------------------------------------------------


def is_tlv_listing(hit: dict) -> bool:
    """THE mandatory exchange gate, in one place. Tel Aviv exchange AND a .TA
    symbol — the suffix also protects the ILA agorot ÷100 rule, which keys off
    `.endswith('.TA')` in collect_prices.currency_for()."""
    return hit.get("exchange") in TLV_EXCHANGES and str(hit.get("symbol", "")).endswith(".TA")


def tase_needing_enrichment(engine) -> list[dict]:
    """Watchlisted TASE securities with no Yahoo ticker yet (UNION of all
    users' watchlists, like every collector)."""
    stmt = (
        select(securities)
        .join(watchlist, watchlist.c.sec_id == securities.c.sec_id)
        .where(securities.c.market == "TASE", securities.c.yahoo_symbol.is_(None))
        .distinct()
    )
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(stmt)]


def has_manual_prices(engine, sec_id: str) -> bool:
    stmt = select(func.count()).select_from(manual_prices).where(manual_prices.c.sec_id == sec_id)
    with engine.connect() as conn:
        return (conn.execute(stmt).scalar() or 0) > 0


def enrich(commit: bool = False) -> None:
    engine = get_engine()
    init_db(engine)
    todo = tase_needing_enrichment(engine)
    log.info(
        "TASE securities needing a ticker: %d%s",
        len(todo), "" if commit else "  [DRY-RUN — no writes]",
    )
    if not todo:
        return
    if len(todo) > MAX_PER_RUN:
        log.info("capping this run at %d of %d (backlog clears over subsequent runs)", MAX_PER_RUN, len(todo))
        todo = todo[:MAX_PER_RUN]

    matched = upgraded = kept_manual_data = no_prices = no_hit = non_tlv = 0
    for sec in todo:
        sec_id, our_name = sec["sec_id"], sec["name"]
        isin = tase_isin(sec_id)
        hits = _yahoo_search(isin, limit=5)
        # First hit that passes the TLV gate; every observed resolution in the
        # scale test had exactly one equity hit, so this is the probed pick
        # made robust to a TEVA-style dual-listing appearing alongside.
        tlv = next((h for h in hits if is_tlv_listing(h)), None)

        if tlv is None:
            if not hits:
                no_hit += 1
                log.info("%s (%s): ISIN %s -> NO-HIT — stays as-is (never guessing)", sec_id, our_name, isin)
            else:
                non_tlv += 1
                h0 = hits[0]
                # The Camtek case: right company, wrong (non-TLV) listing.
                log.warning(
                    "%s (%s): ISIN %s -> %s %r on %r — NOT a Tel Aviv listing, rejected; stays as-is",
                    sec_id, our_name, isin, h0["symbol"], h0["name"], h0["exchange"],
                )
            time.sleep(REQUEST_DELAY)
            continue

        matched += 1
        symbol = tlv["symbol"]
        # Name is logged for human eyeballing — identity is the ISIN + TLV gate.
        log.info(
            "%s (%s): ISIN %s -> MATCH %s  yahoo_name=%r", sec_id, our_name, isin, symbol, tlv["name"],
        )

        # Decide the tier. Never flip a row whose manual prices were entered by
        # hand — that migration is a deliberate human step, not a side effect.
        if has_manual_prices(engine, sec_id):
            kept_manual_data += 1
            new_price_source = None  # untouched
            log.warning(
                "%s (%s): resolved %s but HAS HAND-ENTERED manual_prices — "
                "left on the manual tier; migrate deliberately if desired",
                sec_id, our_name, symbol,
            )
        elif _yfinance_has_prices(symbol):
            new_price_source = "yfinance"  # what collect_prices' auto tier selects on
        else:
            no_prices += 1
            new_price_source = None
            log.info(
                "%s (%s): %s has no usable closes (NaN guard) — symbol stored, stays manual",
                sec_id, our_name, symbol,
            )

        values = {"yahoo_symbol": symbol}
        if new_price_source:
            upgraded += 1
            values["price_source"] = new_price_source
        if commit:
            with engine.begin() as conn:
                conn.execute(update(securities).where(securities.c.sec_id == sec_id).values(**values))
            log.info("%s: wrote %s", sec_id, values)
        else:
            log.info("%s: would write %s", sec_id, values)
        time.sleep(REQUEST_DELAY)

    # The real production resolution rate — the scale test's 92% was measured
    # on a snapshot and is an optimistic ceiling; this line is how we learn.
    log.info(
        "summary: attempted=%d matched=%d (rate %.0f%%) upgraded_to_yfinance=%d "
        "kept_manual_hand_data=%d symbol_only_no_prices=%d no_hit=%d non_tlv=%d%s",
        len(todo), matched, 100.0 * matched / len(todo), upgraded,
        kept_manual_data, no_prices, no_hit, non_tlv,
        "" if commit else "  [DRY-RUN — nothing written]",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m desk.collect_enrich",
        description="Resolve Yahoo tickers for TASE securities via ISIN search (TLV-gated, no-guess).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="resolve and report, write nothing (this is the DEFAULT)")
    mode.add_argument("--commit", action="store_true",
                      help="actually UPDATE securities.yahoo_symbol/price_source")
    args = parser.parse_args()
    enrich(commit=args.commit)


if __name__ == "__main__":
    main()

# Retry policy (reported, per the build brief): unresolved rows (Kenon-style
# foreign incorporation, Yahoo coverage gaps) stay NULL and are re-queried every
# run. With the handful of such rows expected, that is a few gentle requests per
# run — acceptable. If the unresolved set ever grows past ~MAX_PER_RUN, add a
# nullable `enrich_failed_at` column (deliberate schema step) and skip recent
# failures. Do NOT mark failures with a sentinel in yahoo_symbol: collect_prices
# resolve_yahoo_symbol() would treat any non-NULL value there as a real ticker.
