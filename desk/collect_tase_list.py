"""Populate/refresh `tase_securities` — the LOCAL searchable catalogue of TASE
stocks for instant Israeli search in the UI. Cloud collector, WRITE-only
against DESK_DB_URL.

Browserless MAYA access (proven in research/EDGE_SEARCH_FINDINGS.md +
TASE_LIST_FINDINGS.md): plain HTTPS GET with browser-like headers, **no
`Origin`** (a foreign Origin -> 403), no Playwright cookie harvest. Imperva is
in front but does not challenge these API GETs.

Method (see TASE_LIST_FINDINGS.md): there is no one-shot securities dump, so we
  1. enumerate companyIds via companies/autocomplete over curated Hebrew
     prefixes (+ always include watchlist companies), then
  2. per company, companies/<id>/details -> mainSecurityId (the PRIMARY STOCK
     security number) + its securityType; skip bond-only/deleted/no-stock.
Upsert one row per company primary stock (ON CONFLICT(security_number) DO
UPDATE). Coverage is broad but not guaranteed exhaustive; the set grows over
runs (prefixes tunable; onboarding adds resolved securities; live MAYA search
covers the long tail). Slow cadence (daily) — this list changes slowly.

Fail-soft: gate/HTTP/shape failures are logged and skipped, never fatal.
Gentle: paced with a small delay between requests (ToS).
"""
import logging
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from sqlalchemy import select

from desk.db import get_engine, init_db, securities, tase_securities, upsert, watchlist
from desk.maya_client import AUTOCOMPLETE_URL, COMPANY_DETAILS_URL, UA

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_tase_list")

# Browser-like headers WITHOUT Origin (foreign Origin -> Imperva 403).
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    "Referer": "https://maya.tase.co.il/",
}
REQUEST_DELAY = 0.15  # be gentle to a public regulatory feed

# Curated Hebrew 2-char prefixes for autocomplete enumeration. Broad, not
# exhaustive (autocomplete caps at `take`); tune/extend to widen coverage.
CURATED_PREFIXES = [
    "בנ", "טב", "אל", "מל", "שי", "בע", "חב", "אג", "דל", "נכ",
    "מנ", "הפ", "אמ", "בי", "גב", "די", "הא", "וי", "חי", "יש",
    "כל", "לא", "מש", "נת", "סה", "עי", "פז", "צי", "קב", "רב",
    "שב", "תל", "אב", "אר", "גל", "הר", "מד", "סל", "פר", "קר",
]


def _get_json(session, url):
    try:
        r = session.get(url, timeout=25)
    except Exception as e:
        log.warning("GET failed %s: %s", url, e)
        return None
    if r.status_code != 200:
        log.info("GET %s -> HTTP %s", url, r.status_code)
        return None
    try:
        return r.json()
    except Exception:
        return None


def enumerate_company_ids(session) -> dict[int, str]:
    """companyId -> Hebrew name, via autocomplete over the curated prefixes."""
    found: dict[int, str] = {}
    for pref in CURATED_PREFIXES:
        data = _get_json(session, f"{AUTOCOMPLETE_URL}?search={quote(pref)}&take=50")
        if isinstance(data, list):
            for it in data:
                if it.get("type") == "COMPANY" and it.get("key") is not None:
                    try:
                        cid = int(it["key"])
                    except (TypeError, ValueError):
                        continue
                    found.setdefault(cid, it.get("value") or it.get("label") or "")
        time.sleep(REQUEST_DELAY)
    log.info("autocomplete enumeration: %d unique companies", len(found))
    return found


def watchlist_company_ids(engine) -> dict[int, str]:
    """companyId -> name for TASE securities on any watchlist (guaranteed
    coverage — never miss what we already track)."""
    stmt = (
        select(securities.c.maya_company_id, securities.c.name)
        .join(watchlist, watchlist.c.sec_id == securities.c.sec_id)
        .where(securities.c.market == "TASE", securities.c.maya_company_id.isnot(None))
        .distinct()
    )
    out: dict[int, str] = {}
    with engine.connect() as conn:
        for cid, name in conn.execute(stmt):
            if cid is not None:
                out[int(cid)] = name or ""
    return out


def company_primary_stock(session, company_id: int) -> dict | None:
    """companies/<id>/details -> primary-stock row, or None (bond-only/deleted/
    no stock). security_number = mainSecurityId; type from the secrities list."""
    d = _get_json(session, COMPANY_DETAILS_URL.format(company_id=company_id))
    if not isinstance(d, dict):
        return None
    if d.get("isDeleted") or d.get("isBond"):
        return None
    main = d.get("mainSecurityId")
    if main is None:
        return None
    sectype = None
    for sec in d.get("secrities") or []:
        if sec.get("securityId") == main:
            sectype = sec.get("securityType")
            break
    return {
        "security_number": str(main),
        "name": d.get("name") or "",
        "company_id": int(company_id),
        "security_type": sectype,
        "is_primary_stock": True,
    }


def collect() -> None:
    engine = get_engine()
    init_db(engine)
    session = requests.Session()
    session.headers.update(HEADERS)

    companies = enumerate_company_ids(session)
    for cid, name in watchlist_company_ids(engine).items():
        companies.setdefault(cid, name)  # guarantee watchlist coverage
    log.info("companies to resolve: %d", len(companies))

    upserted = skipped = 0
    for cid, fallback_name in companies.items():
        rec = company_primary_stock(session, cid)
        time.sleep(REQUEST_DELAY)
        if rec is None:
            skipped += 1
            continue
        if not rec["name"]:
            rec["name"] = fallback_name
        rec["updated_at"] = datetime.now(timezone.utc)
        with engine.begin() as conn:
            conn.execute(upsert(engine, tase_securities, ["security_number"], rec))
        upserted += 1

    log.info("done: upserted=%d skipped(no primary stock)=%d", upserted, skipped)


if __name__ == "__main__":
    collect()
