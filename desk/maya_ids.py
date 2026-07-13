"""Resolve TASE security numbers -> MAYA companyId and cache them on
`securities.maya_company_id`. Run once per new security; idempotent.

The 2-hop lookup (proven in research/MAYA_FINDINGS.md):
  1. security number -> official name
     via apicontent.tase.co.il/api/search/market?q=<number> (the row whose
     `id` == the number carries the company's official name),
  2. name -> companyId
     via api/v1/companies/autocomplete?search=<name> (item `key` == companyId).

The "drop the last 3 digits" shortcut is NOT used — it coincides for old
listings but is wrong for small caps (Bio-Dvash 1082346 -> 2093, not 1082).

CLI: `python -m desk.maya_ids` resolves every TASE security with
maya_company_id IS NULL and persists the result. Gentle (small delay between
calls); re-running resolves 0 new.
"""
import logging
import time

from sqlalchemy import select, update

from desk.db import get_engine, init_db, securities
from desk.maya_client import (
    AUTOCOMPLETE_URL,
    SEARCH_URL,
    gate_cleared,
    harvest_cookies,
    make_session,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("maya_ids")

REQUEST_DELAY = 1.0  # be gentle: space out calls to a public regulatory feed


def _search(session, query: str) -> list[dict]:
    r = session.get(SEARCH_URL, params={"q": query, "culture": "he-IL"}, timeout=30)
    if r.status_code != 200:
        log.warning("search q=%r -> HTTP %s", query, r.status_code)
        return []
    try:
        return (r.json() or {}).get("data") or []
    except Exception as e:
        log.warning("search q=%r -> bad JSON: %s", query, e)
        return []


def _official_name(session, security_number: str) -> str | None:
    """Hop 1: the name on the security ('מניות') row whose id == the number."""
    rows = _search(session, security_number)
    for row in rows:
        if str(row.get("id")) == str(security_number):
            return row.get("name")
    return None


def _company_id_for_name(session, name: str) -> int | None:
    """Hop 2: autocomplete by name; the matching item's `key` is the companyId."""
    r = session.get(AUTOCOMPLETE_URL, params={"search": name, "take": 8}, timeout=30)
    if r.status_code != 200:
        log.warning("autocomplete search=%r -> HTTP %s", name, r.status_code)
        # Fall back to search/market's company row (url contains /companies/).
        for row in _search(session, name):
            if "/companies/" in (row.get("url") or ""):
                try:
                    return int(row["id"])
                except (KeyError, TypeError, ValueError):
                    pass
        return None
    try:
        items = r.json() or []
    except Exception as e:
        log.warning("autocomplete search=%r -> bad JSON: %s", name, e)
        return []
    # Prefer an exact company-name match, else the first COMPANY-type item.
    best = None
    for item in items:
        if item.get("type") == "COMPANY" and item.get("key") is not None:
            if item.get("value") == name or item.get("label") == name:
                return int(item["key"])
            if best is None:
                best = int(item["key"])
    return best


def resolve_company_id(session, security_number: str) -> int | None:
    """Full 2-hop resolution: security number -> companyId (or None)."""
    name = _official_name(session, security_number)
    if not name:
        log.info("  %s: no official name from search — cannot resolve", security_number)
        return None
    cid = _company_id_for_name(session, name)
    if cid is None:
        log.info("  %s (%s): name found but no companyId", security_number, name)
        return None
    log.info("  %s -> %s (companyId %s)", security_number, name, cid)
    return cid


def _tase_needing_resolution(engine) -> list[dict]:
    stmt = select(securities).where(
        securities.c.market == "TASE", securities.c.maya_company_id.is_(None)
    )
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(stmt)]


def backfill(engine=None) -> None:
    engine = engine or get_engine()
    init_db(engine)
    todo = _tase_needing_resolution(engine)
    if not todo:
        log.info("no TASE securities need a companyId — nothing to do")
        return

    cookies = harvest_cookies()
    if not gate_cleared(cookies):
        log.warning(
            "MAYA bot gate NOT cleared (cookies=%d) — cannot resolve now; try again",
            len(cookies),
        )
        return
    session = make_session(cookies)
    log.info("resolving %d TASE securities", len(todo))

    resolved = failed = 0
    for sec in todo:
        cid = resolve_company_id(session, sec["sec_id"])
        if cid is None:
            failed += 1
            log.warning("FAILED: %s (%s)", sec["sec_id"], sec["name"])
        else:
            with engine.begin() as conn:
                conn.execute(
                    update(securities)
                    .where(securities.c.sec_id == sec["sec_id"])
                    .values(maya_company_id=cid)
                )
            resolved += 1
        time.sleep(REQUEST_DELAY)

    log.info("done: resolved=%d failed=%d", resolved, failed)


if __name__ == "__main__":
    backfill()
