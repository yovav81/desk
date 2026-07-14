"""Populate/refresh `tase_securities` — the LOCAL searchable catalogue of TASE
stocks for instant Israeli search in the UI. Cloud collector, WRITE-only
against DESK_DB_URL.

Browserless MAYA access (proven in research/EDGE_SEARCH_FINDINGS.md +
TASE_LIST_FINDINGS.md): plain HTTPS GET with browser-like headers, **no
`Origin`** (a foreign Origin -> 403), no Playwright cookie harvest. Imperva is
in front but does not challenge these API GETs.

Enumeration = **companyId sweep** (replaces the earlier prefix-autocomplete
method, which capped at 50/prefix and missed companies — e.g. only 1 bank for
"בנק"). Valid TASE companyIds cluster ~110..2585; we sweep a bounded range and
call `companies/<id>/details` for each to get the Hebrew name + `mainSecurityId`
(the PRIMARY STOCK security number) + its securityType. Bond-only / deleted /
no-stock companies (mainSecurityId null) are skipped. Watchlist companies are
always included. Upsert ON CONFLICT(security_number) DO UPDATE.

The sweep is ~2,500 requests, so:
- **paced** (small delay) + **retry once** on transient (network/5xx) errors,
- **resumable**: companyIds whose stock is already fresh in the table (updated
  within FRESH_HOURS) are skipped, so an interrupted run resumes cheaply and a
  same-day re-run is fast. A daily run (>FRESH_HOURS apart) refreshes everything.
- **progress-logged** every PROGRESS_EVERY companies during the multi-minute run.

Fail-soft: HTTP/shape failures are logged and skipped, never fatal.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import select

from desk.db import get_engine, init_db, securities, tase_securities, upsert, watchlist
from desk.maya_client import COMPANY_DETAILS_URL, UA

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_tase_list")

# Browser-like headers WITHOUT Origin (foreign Origin -> Imperva 403).
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    "Referer": "https://maya.tase.co.il/",
}
REQUEST_DELAY = 0.15   # be gentle to a public regulatory feed
SWEEP_MIN = 100        # valid TASE companyIds observed ~110..2585
SWEEP_MAX = 2650
FRESH_HOURS = 20       # skip company_ids refreshed within this window (resumable)
PROGRESS_EVERY = 100


def _get_json(session, url, retries: int = 1):
    """GET JSON. Returns None on 4xx (e.g. 404 = invalid id) with no retry;
    retries once on network error / 5xx (transient)."""
    for attempt in range(retries + 1):
        try:
            r = session.get(url, timeout=25)
        except Exception as e:
            if attempt < retries:
                time.sleep(1.0)
                continue
            log.warning("GET failed %s: %s", url, e)
            return None
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        if r.status_code >= 500 and attempt < retries:
            time.sleep(1.0)
            continue
        return None  # 4xx (invalid/absent id) — expected during the sweep
    return None


def _fresh_company_ids(engine) -> set[int]:
    """company_ids whose row was updated within FRESH_HOURS — skip in the sweep."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=FRESH_HOURS)
    fresh: set[int] = set()
    with engine.connect() as conn:
        rows = conn.execute(
            select(tase_securities.c.company_id, tase_securities.c.updated_at)
        ).all()
    for cid, updated in rows:
        if cid is None or updated is None:
            continue
        if isinstance(updated, str):
            try:
                updated = datetime.fromisoformat(updated)
            except ValueError:
                continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated >= cutoff:
            fresh.add(int(cid))
    return fresh


def watchlist_company_ids(engine) -> set[int]:
    """companyIds of TASE securities on any watchlist — guaranteed coverage."""
    stmt = (
        select(securities.c.maya_company_id)
        .join(watchlist, watchlist.c.sec_id == securities.c.sec_id)
        .where(securities.c.market == "TASE", securities.c.maya_company_id.isnot(None))
        .distinct()
    )
    with engine.connect() as conn:
        return {int(cid) for (cid,) in conn.execute(stmt) if cid is not None}


def company_primary_stock(session, company_id: int) -> dict | None:
    """companies/<id>/details -> primary-stock row, or None (bond-only/deleted/
    no stock/invalid id). security_number = mainSecurityId; type from secrities."""
    d = _get_json(session, COMPANY_DETAILS_URL.format(company_id=company_id))
    if not isinstance(d, dict) or not d.get("companyId"):
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
    # Prefer the full registered name (`longName` = "בנק לאומי לישראל בע\"מ") over
    # the short brand ("לאומי"): it contains BOTH the brand and words like "בנק",
    # so a search for either matches. Banks etc. are otherwise unfindable by "בנק".
    name = d.get("longName") or d.get("name") or ""
    return {
        "security_number": str(main),
        "name": name,
        "company_id": int(company_id),
        "security_type": sectype,
        "is_primary_stock": True,
    }


def collect() -> None:
    engine = get_engine()
    init_db(engine)
    session = requests.Session()
    session.headers.update(HEADERS)

    fresh = _fresh_company_ids(engine)
    watch = watchlist_company_ids(engine)  # always processed even if "fresh"
    # Sweep the bounded companyId range; always include watchlist ids.
    ids = sorted(set(range(SWEEP_MIN, SWEEP_MAX + 1)) | watch)
    log.info(
        "sweep %d..%d (%d ids), %d already fresh -> skip, watchlist forced=%d",
        SWEEP_MIN, SWEEP_MAX, len(ids), len(fresh - watch), len(watch),
    )

    upserted = skipped_nostock = skipped_fresh = 0
    processed = 0
    for cid in ids:
        if cid in fresh and cid not in watch:
            skipped_fresh += 1
            continue
        rec = company_primary_stock(session, cid)
        time.sleep(REQUEST_DELAY)
        processed += 1
        if rec is None:
            skipped_nostock += 1
        else:
            rec["updated_at"] = datetime.now(timezone.utc)
            with engine.begin() as conn:
                conn.execute(upsert(engine, tase_securities, ["security_number"], rec))
            upserted += 1
        if processed % PROGRESS_EVERY == 0:
            log.info("  …processed=%d upserted=%d no-stock=%d (at id %d)", processed, upserted, skipped_nostock, cid)

    log.info(
        "done: upserted=%d skipped(no primary stock)=%d skipped(fresh)=%d",
        upserted, skipped_nostock, skipped_fresh,
    )


if __name__ == "__main__":
    collect()
