# DESK

Watchlist dashboard (US tickers + Israeli securities, stocks & bonds):
current/MTD/YTD performance + aggregated news (web + forwarded emails).
Filings (SEC/MAYA/MAGNA) arrive later via a read-only link to an existing
system — always out of scope for this project.

Will eventually be a hosted, multi-user service (employees, mobile-friendly).
Phase 0 = data source investigation (`research/FINDINGS.md`). Phase 1 (current)
= data foundation: DB schema, securities mapping, collectors — no UI, no
price math. Phase 2 = pricing + hosted Streamlit dashboard. See `TODO.md`.

## Folder isolation

All work stays inside `C:\desk`. Never read or write `C:\invest`,
`C:\screener`, `C:\wealth`, `C:\nadlan` — unrelated projects on this machine.

## Architecture

- `desk/db.py` — SQLAlchemy Core schema + `init_db()`. Portable SQL only (no
  SQLite-specific pragmas) so the same code runs on local SQLite
  (`DESK_DB_URL` unset → `sqlite:///desk.db`) and hosted Postgres
  (`DESK_DB_URL=postgresql+psycopg://...`). Dedup uses
  `INSERT ... ON CONFLICT DO NOTHING` via `db.insert_ignore()`, which
  branches on `engine.dialect.name` — keep using that helper rather than
  raw `.insert()` for any table with a UNIQUE constraint.
- `desk/securities.py` — loads `data/securities.csv`, lookup only. Does not
  touch prices.
- `desk/collect_news.py`, `desk/collect_email.py` — **cloud collectors,
  WRITE-only** against `DESK_DB_URL`. Meant to run unattended on a schedule
  (`.github/workflows/collect.yml`, every 15 min). The eventual dashboard is
  **READ-only** against the same DB — never merge write/collection logic
  into dashboard code.
- `data/securities.csv` — the security-number/symbol/name/type/market
  mapping. TASE has no scriptable export (WAF-blocked, see Phase 0
  findings); seeded via manual browser export or (future) TASE DataHub's
  free "Securities (Basic)" API product.

## Data model rules

- `watchlist` is **per-user** (FK to `users`). `securities`, `news`, and
  `emails` are **shared across all users** — collectors always operate on
  the union of every user's watchlist, never a single user's.
- This phase stores **raw data only**: no LLM calls, no summarization, no
  scoring. `news.summary` stays NULL; nothing paraphrases article or email
  content.
- `news.url` and `emails.message_id` are UNIQUE — the dedup guards that
  make collectors safe to re-run on a cron. Don't relax these constraints.

## Known pitfalls

- **yfinance `.TA` tickers report price in ILA (agorot), not ILS** — divide
  by 100. Not yet handled anywhere in Phase 1 code (lookup/collection only);
  must land wherever Phase 2 computes prices.
- TASE bonds have no free API source (Yahoo doesn't carry them; TASE's own
  endpoints are WAF-blocked; DataHub's EOD bond product is paid, ~$100/mo).
  Still an open decision — see `TODO.md`.
- `gh` CLI is not installed on this machine; GitHub repo/secrets setup for
  this project has been manual/undone so far — don't assume a remote exists.

## Secrets

Read from environment only, never hardcoded: `DESK_DB_URL`,
`DESK_DEFAULT_USER`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`. Documented in
`.env.example` / `README.md`. In CI these are GitHub Actions secrets.
