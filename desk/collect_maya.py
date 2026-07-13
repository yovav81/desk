"""Collect MAYA company disclosure announcements for every TASE security on any
user's watchlist. Cloud collector, WRITE-only against DESK_DB_URL — meant to
run unattended on the same 15-min schedule as the other collectors.

Pattern-replicated from the 2b pre-check (research/MAYA_FINDINGS.md); this code
reads no other project. Announcements feed only (headline + date + document
link) — no financial field codes, no LLM calls, no summaries.

Flow per run:
  1. one cookie harvest (headless Chromium) -> requests.Session. If the gate
     is not cleared, log and exit 0 (fail-soft, like collect_email with no
     creds) — never crash the workflow.
  2. for each watchlisted TASE security with a cached maya_company_id, POST
     api/v1/reports/companies and INSERT ... ON CONFLICT(source, maya_id) DO
     NOTHING. Securities without a companyId are skipped (hint: run maya_ids).
  3. fail-soft on shape changes: a malformed response for one company is logged
     and skipped, not fatal.

Dedup guard: filings UNIQUE(source, maya_id) — safe to re-run on a cron.
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from desk.db import filings, get_engine, init_db, insert_ignore, securities, watchlist
from desk.maya_client import (
    REPORTS_URL,
    doc_url_from_attachments,
    gate_cleared,
    harvest_cookies,
    make_session,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_maya")

PAGE_LIMIT = 20


def watchlisted_tase_securities(engine) -> list[dict]:
    """Distinct TASE securities across the UNION of all users' watchlists."""
    stmt = (
        select(securities)
        .join(watchlist, watchlist.c.sec_id == securities.c.sec_id)
        .where(securities.c.market == "TASE")
        .distinct()
    )
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(stmt)]


def _parse_published(raw) -> datetime | None:
    if not raw:
        return None
    try:
        # e.g. "2026-07-13T17:29:02.88" (naive, Israel local) — store as-is UTC-tagged.
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def fetch_company_reports(session, company_id: int) -> list[dict] | None:
    """POST the per-company feed. Returns the list of announcements, or None on
    HTTP/JSON/shape failure (caller skips, doesn't crash)."""
    body = {"pageNumber": 1, "companyId": int(company_id), "limit": PAGE_LIMIT, "offset": 0}
    try:
        r = session.post(
            REPORTS_URL,
            data=json.dumps(body),
            timeout=30,
            headers={"Content-Type": "application/json", "Origin": "https://maya.tase.co.il"},
        )
    except Exception as e:
        log.warning("companyId %s: request failed: %s", company_id, e)
        return None
    if r.status_code != 200:
        log.warning("companyId %s: HTTP %s", company_id, r.status_code)
        return None
    try:
        data = r.json()
    except Exception as e:
        log.warning("companyId %s: bad JSON: %s", company_id, e)
        return None
    rows = data if isinstance(data, list) else data.get("reports") or data.get("data")
    if not isinstance(rows, list):
        log.warning("companyId %s: unexpected JSON shape, skipping", company_id)
        return None
    return rows


def collect() -> None:
    engine = get_engine()
    init_db(engine)
    secs = watchlisted_tase_securities(engine)
    log.info("TASE securities on watchlists: %d", len(secs))

    cookies = harvest_cookies()
    cleared = gate_cleared(cookies)
    log.info("cookie harvest: %d cookies, gate cleared: %s", len(cookies), cleared)
    if not cleared:
        # Fail-soft: an Imperva challenge from this IP is not a workflow failure.
        log.warning("MAYA bot gate not cleared — skipping this run (exit 0)")
        return
    session = make_session(cookies)

    total_new = 0
    for sec in secs:
        cid = sec.get("maya_company_id")
        if cid is None:
            log.info("%s (%s): no maya_company_id — skipping (run `python -m desk.maya_ids`)", sec["sec_id"], sec["name"])
            continue
        rows = fetch_company_reports(session, cid)
        if rows is None:
            continue

        new_count = 0
        with engine.begin() as conn:
            for r in rows:
                maya_id = r.get("id")
                title = r.get("title")
                if maya_id is None or not title:
                    continue  # skip malformed row, keep going
                stmt = insert_ignore(engine, filings, ["source", "maya_id"]).values(
                    sec_id=sec["sec_id"],
                    source="maya",
                    maya_id=maya_id,
                    title=title,
                    published_at=_parse_published(r.get("publishDate")),
                    doc_url=doc_url_from_attachments(r.get("attachments")),
                )
                if conn.execute(stmt).rowcount:
                    new_count += 1

        total_new += new_count
        log.info("%s (%s, companyId %s): fetched=%d new=%d", sec["sec_id"], sec["name"], cid, len(rows), new_count)

    log.info("done: total new filings=%d", total_new)


if __name__ == "__main__":
    collect()
