"""Collect SEC EDGAR filings for every US security on any user's watchlist.
Cloud collector, WRITE-only against DESK_DB_URL — meant to run unattended on the
same 15-min schedule as the other collectors.

Structure mirrors collect_maya.py (union-of-watchlists selection, skip-with-a-
hint on a missing id, fail-soft per company, insert_ignore dedup, rowcount-based
counting). Endpoint choices are from research/SEC_COLLECTOR_FINDINGS.md.

Flow per run:
  1. for each watchlisted US security with a cached cik, GET
     data.sec.gov/submissions/CIK##########.json (one ~28 KB call per company,
     no auth, no browser — unlike MAYA there is no bot gate).
  2. keep only allowlisted forms filed in the last LOOKBACK_DAYS, and
     INSERT ... ON CONFLICT (source, accession_no) DO NOTHING.
  3. fail-soft on shape changes: a malformed response for one company is logged
     and skipped, not fatal. Securities without a cik are skipped (hint: run
     sec_ids).

Why an allowlist and not a blocklist: unfiltered, the feed is ~59% Form 4
insider noise (measured on Apple's 1,000 most recent). An allowlist also fails
safe — an unknown new form type is skipped, never spammed into the feed.

Dedup guard: filings UNIQUE(source, accession_no) — safe to re-run on a cron.
maya_id stays NULL on every row written here; the MAYA guard is untouched.

CLI (SAFE BY DEFAULT — dry-run unless --commit):
    python -m desk.collect_sec            # dry-run: fetch + report, write nothing
    python -m desk.collect_sec --commit   # actually INSERT
"""
import argparse
import json
import logging
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone

from sqlalchemy import select

from desk.db import filings, get_engine, init_db, insert_ignore, securities, watchlist
from desk.sec_ids import cik_to_path, user_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_sec")

SUBMISSIONS_URL = "https://data.sec.gov/submissions/{path}.json"
# SEC's published fair-access limit is 10 req/s across *.sec.gov. With 5
# securities this is a formality, but stay well under it.
REQUEST_DELAY = 0.2
LOOKBACK_DAYS = 90

# Hebrew label per form. Rendered as "<label> (<form>)"; amendments append the
# suffix below. SAP SE is a foreign private issuer — it files 20-F/6-K and never
# 10-K/10-Q, which is exactly why those two are here.
FORM_TITLES_HE = {
    "10-K": "דוח שנתי",
    "10-Q": "דוח רבעוני",
    "8-K": "דיווח מיידי",
    "DEF 14A": "זימון אסיפה",
    "20-F": "דוח שנתי",
    "6-K": "דיווח מיידי",
}
# Exact matches; a form ending in "/A" (an amendment, e.g. "10-K/A") is kept if
# its base form is allowlisted.
FORM_ALLOWLIST = frozenset({"10-K", "10-Q", "8-K", "DEF 14A", "20-F", "6-K"})
AMENDMENT_SUFFIX = "/A"
AMENDMENT_LABEL = " — תיקון"


def watchlisted_us_securities(engine) -> list[dict]:
    """Distinct US securities across the UNION of all users' watchlists."""
    stmt = (
        select(securities)
        .join(watchlist, watchlist.c.sec_id == securities.c.sec_id)
        .where(securities.c.market == "US")
        .distinct()
    )
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(stmt)]


def _existing_accessions(engine) -> set[str]:
    """Accession numbers already stored — lets --dry-run report what it WOULD
    insert instead of just what it fetched. Not used on the commit path, where
    ON CONFLICT is the real (and only) guard."""
    stmt = select(filings.c.accession_no).where(filings.c.source == "sec")
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(stmt) if r[0]}


def split_form(form: str) -> tuple[str, bool]:
    """'10-K/A' -> ('10-K', True); '8-K' -> ('8-K', False)."""
    if form.endswith(AMENDMENT_SUFFIX):
        return form[: -len(AMENDMENT_SUFFIX)], True
    return form, False


def is_allowed(form: str) -> bool:
    base, _ = split_form(form)
    return base in FORM_ALLOWLIST


def title_for(form: str) -> str:
    """Hebrew title for a form code. Falls back to the raw form code rather than
    skipping or crashing if a form is somehow not in the map."""
    base, amended = split_form(form)
    label = FORM_TITLES_HE.get(base)
    if label is None:
        return form  # raw code — honest, never a crash
    return f"{label} ({base})" + (AMENDMENT_LABEL if amended else "")


def doc_url_for(cik: int, accession_no: str, primary_document: str) -> str:
    """URL of the actual filing document on sec.gov.

    Note the asymmetry: the Archives path uses the BARE cik, while
    data.sec.gov/submissions wants the zero-padded form (cik_to_path). Padding
    lives only in sec_ids.cik_to_path — never re-implemented here.
    """
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession_no.replace('-', '')}/{primary_document}"
    )


def _parse_published(acceptance: str | None, filing_date: str | None) -> datetime | None:
    """Prefer acceptanceDateTime (carries a real time); fall back to filingDate
    at midnight UTC. Returns None if neither parses — the caller then skips the
    filing rather than inventing a date."""
    if acceptance:
        try:
            # ISO 8601; may carry 'Z', which fromisoformat rejects before 3.11.
            return datetime.fromisoformat(acceptance.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            pass
    if filing_date:
        try:
            d = datetime.strptime(filing_date, "%Y-%m-%d").date()
            return datetime.combine(d, dtime.min, tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    return None


def _filing_date(filing_date: str | None) -> date | None:
    try:
        return datetime.strptime(filing_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def fetch_submissions(cik: int, ua: str) -> dict | None:
    """GET the per-company submissions JSON. Returns the parsed dict, or None on
    HTTP/JSON failure (caller skips, doesn't crash)."""
    url = SUBMISSIONS_URL.format(path=cik_to_path(cik))
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        log.warning("cik %s: HTTP %s from %s", cik, e.code, url)
        return None
    except Exception as e:
        log.warning("cik %s: request/JSON failed: %s", cik, e)
        return None


def recent_filings(data: dict, cik: int) -> list[dict] | None:
    """Flatten filings.recent (PARALLEL ARRAYS, not a list of objects) into
    dicts. Returns None if the shape isn't what we expect."""
    try:
        rec = data["filings"]["recent"]
    except (KeyError, TypeError):
        log.warning("cik %s: no filings.recent in response, skipping", cik)
        return None
    required = ("accessionNumber", "form", "filingDate", "primaryDocument")
    missing = [k for k in required if not isinstance(rec.get(k), list)]
    if missing:
        log.warning("cik %s: filings.recent missing %s, skipping", cik, ",".join(missing))
        return None

    acceptance = rec.get("acceptanceDateTime")
    n = len(rec["accessionNumber"])
    out = []
    for i in range(n):
        out.append({
            "accession_no": rec["accessionNumber"][i],
            "form": rec["form"][i],
            "filing_date": rec["filingDate"][i],
            "primary_document": rec["primaryDocument"][i],
            "acceptance": acceptance[i] if isinstance(acceptance, list) and i < len(acceptance) else None,
        })
    return out


def collect(commit: bool = False) -> None:
    engine = get_engine()
    init_db(engine)
    secs = watchlisted_us_securities(engine)
    log.info("US securities on watchlists: %d%s", len(secs), "" if commit else "  [DRY-RUN — no writes]")
    if not secs:
        return

    ua = user_agent()  # SystemExit with a clear message if SEC_USER_AGENT is unset
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=LOOKBACK_DAYS)
    already = set() if commit else _existing_accessions(engine)

    total_new = 0
    for sec in secs:
        cik = sec.get("cik")
        if cik is None:
            log.info("%s (%s): no cik — skipping (run `python -m desk.sec_ids --commit`)", sec["sec_id"], sec["name"])
            continue

        data = fetch_submissions(cik, ua)
        time.sleep(REQUEST_DELAY)
        if data is None:
            continue
        rows = recent_filings(data, cik)
        if rows is None:
            continue

        # No assumption about ordering: filter the whole block rather than
        # break-ing on the first old row.
        kept = []
        for r in rows:
            if not is_allowed(r["form"]):
                continue  # silently: Form 4 et al are the overwhelming majority
            fdate = _filing_date(r["filing_date"])
            if fdate is None:
                log.warning("%s: %s has an unparseable filingDate %r — skipped",
                            sec["sec_id"], r["accession_no"], r["filing_date"])
                continue
            if fdate < cutoff:
                continue
            kept.append(r)

        new_count = 0
        for r in kept:
            # Never fabricate: a filing missing the bits its identity/URL need is
            # skipped and logged, not patched up.
            if not r["accession_no"] or not r["primary_document"]:
                log.warning("%s: filing %r missing accession/primaryDocument — skipped",
                            sec["sec_id"], r["accession_no"] or r["form"])
                continue
            published = _parse_published(r["acceptance"], r["filing_date"])
            if published is None:
                log.warning("%s: %s has no usable date — skipped", sec["sec_id"], r["accession_no"])
                continue

            values = dict(
                sec_id=sec["sec_id"],
                source="sec",
                maya_id=None,  # SEC rows never carry one; its guard stays untouched
                accession_no=r["accession_no"],
                title=title_for(r["form"]),
                published_at=published,
                doc_url=doc_url_for(cik, r["accession_no"], r["primary_document"]),
            )
            if not commit:
                if r["accession_no"] not in already:
                    new_count += 1
                    log.info("   would add %s  %s  %s", r["filing_date"], r["form"], values["title"])
                continue
            with engine.begin() as conn:
                stmt = insert_ignore(engine, filings, ["source", "accession_no"]).values(**values)
                if conn.execute(stmt).rowcount:
                    new_count += 1

        total_new += new_count
        log.info(
            "%s (%s, cik %s): seen=%d kept=%d %s=%d",
            sec["sec_id"], sec["name"], cik, len(rows), len(kept),
            "inserted" if commit else "would_insert", new_count,
        )

    if commit:
        log.info("done: total new filings=%d", total_new)
    else:
        log.info("DRY-RUN done: would insert=%d — nothing written. Re-run with --commit to persist.", total_new)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m desk.collect_sec",
        description="Collect SEC EDGAR filings for watchlisted US securities.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="fetch and report, write nothing (this is the DEFAULT)")
    mode.add_argument("--commit", action="store_true",
                      help="actually INSERT filings rows")
    args = parser.parse_args()
    collect(commit=args.commit)


if __name__ == "__main__":
    main()
