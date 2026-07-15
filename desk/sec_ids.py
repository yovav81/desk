"""Resolve US tickers -> SEC EDGAR CIK and cache them on `securities.cik`.
Run once per new US security; idempotent.

One hop, no scraping: SEC publishes the official mapping as a static file
(https://www.sec.gov/files/company_tickers.json — ~779 KB, ~10,400 entries),
so a ticker resolves by exact lookup. Matching is EXACT on the uppercased
ticker; an unmatched ticker is reported loudly and left NULL — never guessed
(the no-guess policy, same as onboarding.py).

SEC rejects requests without a descriptive User-Agent (measured: a generic or
absent UA -> HTTP 403). It is read from SEC_USER_AGENT rather than hardcoded,
so the contact address stays out of the repo.

CIK PADDING: the DB stores a plain INTEGER. The 10-digit zero-padded form is an
EDGAR URL detail and lives in exactly ONE place — `cik_to_path()` below. The
collector must import that, never re-pad.

CLI (SAFE BY DEFAULT — dry-run unless --commit):
    python -m desk.sec_ids                # dry-run: resolve + report, write nothing
    python -m desk.sec_ids --commit       # actually UPDATE securities.cik
Re-running after a successful commit resolves 0 (the `cik IS NULL` filter).

See research/SEC_COLLECTOR_FINDINGS.md (D5, D6).
"""
import argparse
import json
import logging
import os
import urllib.error
import urllib.request

from sqlalchemy import text

from desk.db import get_engine, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sec_ids")

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# `securities.cik` was added by sql/002_sec_collector.sql but is NOT yet declared
# on the SQLAlchemy Table in desk/db.py, so securities.c.cik does not exist and
# select()/update() cannot name it. Raw SQL until db.py catches up (a later step
# — fresh DBs created by init_db() still lack the column entirely).
_SELECT_TODO = text(
    "select sec_id, symbol, name from securities"
    " where market = 'US' and cik is null order by symbol"
)
_UPDATE_CIK = text("update securities set cik = :cik where sec_id = :sec_id")


def cik_to_path(cik: int) -> str:
    """CIK -> the zero-padded form EDGAR URLs need, e.g. 320193 -> 'CIK0000320193'.

    THE ONLY PLACE PADDING HAPPENS. data.sec.gov/submissions/ wants the padded
    10-digit form; the Archives document path wants the BARE int. Storing the
    int and padding here keeps that asymmetry in one function — import this,
    never re-implement it.
    """
    return f"CIK{int(cik):010d}"


def user_agent() -> str:
    """The descriptive UA SEC requires. Hard failure if unset — this is an
    interactive CLI, not a cron collector, so an unset var is operator error to
    surface now, not a run to silently skip."""
    ua = (os.environ.get("SEC_USER_AGENT") or "").strip()
    if not ua:
        raise SystemExit(
            "SEC_USER_AGENT is not set.\n"
            "SEC EDGAR returns HTTP 403 to requests without a descriptive "
            "User-Agent naming a contact. Set it, then re-run:\n"
            '  PowerShell:  $env:SEC_USER_AGENT = "DESK watchlist (contact: you@example.com)"'
        )
    return ua


def fetch_ticker_map(ua: str) -> dict[str, int]:
    """Fetch SEC's official ticker -> CIK map as {TICKER: cik_int}.

    Deliberately NOT reused from desk.onboarding._load_sec_tickers: importing
    that module drags in yfinance + pandas (via collect_prices) for a lookup
    this small, and its User-Agent is hardcoded, which this CLI must not be.

    Note ticker -> CIK is many-to-one (e.g. GOOGL/GOOG/GOOGM/GOOGN all map to
    1652044); keying by ticker is therefore the right direction and lossless.
    """
    req = urllib.request.Request(SEC_TICKERS_URL, headers={"User-Agent": ua})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise SystemExit(
                f"SEC returned HTTP 403 for {SEC_TICKERS_URL}.\n"
                f"That is the UA check. Current SEC_USER_AGENT={ua!r} — it must be "
                "descriptive and name a contact (a generic agent is rejected)."
            ) from e
        raise SystemExit(f"SEC returned HTTP {e.code} for {SEC_TICKERS_URL}") from e
    except Exception as e:
        raise SystemExit(f"could not fetch {SEC_TICKERS_URL}: {e}") from e

    out: dict[str, int] = {}
    for v in raw.values():
        ticker = str(v.get("ticker", "")).strip().upper()
        cik = v.get("cik_str")
        if ticker and cik is not None:
            out[ticker] = int(cik)
    log.info("SEC ticker map: %d entries", len(out))
    return out


def _us_needing_resolution(engine) -> list[dict]:
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(_SELECT_TODO)]


def backfill(engine=None, commit: bool = False) -> None:
    engine = engine or get_engine()
    init_db(engine)
    todo = _us_needing_resolution(engine)
    if not todo:
        log.info("no US securities need a CIK — nothing to do")
        return

    ua = user_agent()
    tickers = fetch_ticker_map(ua)
    log.info(
        "resolving %d US securities%s", len(todo), "" if commit else "  [DRY-RUN — no writes]"
    )

    resolved = failed = 0
    for sec in todo:
        symbol = (sec["symbol"] or "").strip().upper()
        cik = tickers.get(symbol)
        if cik is None:
            failed += 1
            # Loud, and left NULL: a near-match would be a guess about identity.
            log.warning(
                "NOT FOUND: %s (%s) — no exact ticker match in SEC's map; cik left NULL",
                sec["symbol"], sec["name"],
            )
            continue

        log.info("  %s -> %s (%s)  %s", symbol, cik, cik_to_path(cik), sec["name"])
        if commit:
            with engine.begin() as conn:
                conn.execute(_UPDATE_CIK, {"cik": cik, "sec_id": sec["sec_id"]})
        resolved += 1

    if commit:
        log.info("done: resolved=%d failed=%d", resolved, failed)
    else:
        log.info(
            "DRY-RUN done: would resolve=%d failed=%d — nothing written. "
            "Re-run with --commit to persist.",
            resolved, failed,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m desk.sec_ids",
        description="Resolve US tickers to SEC CIKs and cache them on securities.cik.",
    )
    # Dry-run is the default; writing takes an explicit --commit. Mutually
    # exclusive so `--dry-run --commit` is a clean error, not a silent winner.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true",
        help="resolve and report, write nothing (this is the DEFAULT)",
    )
    mode.add_argument(
        "--commit", action="store_true",
        help="actually UPDATE securities.cik for exact ticker matches",
    )
    args = parser.parse_args()
    backfill(commit=args.commit)


if __name__ == "__main__":
    main()
