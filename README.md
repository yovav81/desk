# DESK

Personal/team watchlist dashboard: current/MTD/YTD performance (US tickers +
Israeli securities, stocks & bonds) plus aggregated news (web + forwarded
emails). Filings (SEC/MAYA/MAGNA) arrive later via a read-only link to an
existing system — out of scope here.

Phase 0 (`research/FINDINGS.md`) verified free data sources. **Phase 1**
built the data foundation: a Postgres-ready DB schema, an
Israeli-securities mapping, and two scheduled collectors (news, email).
**Phase 2a** (this state) adds a two-tier price collector. **No
dashboard/UI yet** — next up are the MAYA filings collector (2b) and a
React UI (2c).

## Data model

- `users` / `securities` / `watchlist` — **watchlist is per-user** (each row
  ties a user to a security); `securities` and its downstream data
  (`news`, `emails`) are **shared across all users**. Collectors always
  operate on the union of every user's watchlist, not one user at a time.
- `news` and `emails` each have a UNIQUE column (`url`, `message_id`) used
  as a dedup guard via `INSERT ... ON CONFLICT DO NOTHING` — safe to
  re-run collectors on a schedule.
- No `summary`/scoring columns are populated — this phase stores raw data
  only, no LLM calls, no interpretation.

## Database

`desk/db.py` uses SQLAlchemy Core with plain, portable SQL (no
SQLite-specific pragmas), so the same schema works locally and hosted:

- Local dev (default, unset `DESK_DB_URL`): `sqlite:///desk.db`
- Hosted (Phase 2+): a Postgres URL, e.g. from a free tier at Supabase or
  Neon: `postgresql+psycopg://user:password@host:5432/dbname`

The SQLite file is **not** committed to git (see `.gitignore`) — the
hosted Postgres DB is meant to be the actual source of truth once it
exists; local SQLite is only a dev convenience.

Run `python -m desk.db` to create tables and seed the default user
(from `DESK_DEFAULT_USER`, fallback `"owner"`) against `DESK_DB_URL`.

## Securities mapping

`data/securities.csv` (columns: `sec_id,symbol,name,asset_type,market,price_source`)
maps a security number or US symbol to a yfinance symbol, name, type
(stock/bond), and market (US/TASE). `desk/securities.py` loads it and
looks up by exact symbol/sec_id or name substring.

**Seeding it today:** TASE has no scriptable CSV/JSON export (see Phase 0
findings — every endpoint sits behind a WAF that blocks non-browser
clients). Seed rows by manually exporting the list from
`market.tase.co.il/he/market_data/securities/data/all` in a browser.
Longer-term, TASE DataHub's **"Securities (Basic)"** API product is listed
free in the official price list, but needs a portal signup (Phase 2+ decision).

**Known pitfall (Phase 2, not handled by this phase):** yfinance quotes
`.TA` tickers in **ILA (agorot)**, not ILS — divide by 100 to get shekels.
`desk/securities.py` is lookup-only and does not touch prices.

**Bonds** remain an open problem (see `research/FINDINGS.md` — Yahoo
doesn't carry TASE bonds; DataHub's EOD bond product is paid, ~$100/mo).

## Collectors

Both collectors are idempotent (safe to re-run) and touch only `DESK_DB_URL`.

- **`desk/collect_news.py`** — for every security on any user's watchlist,
  queries Google News RSS (Hebrew/Israel for `market=TASE`, English/US
  otherwise) and inserts new items. For better Israeli results, keep
  `name` in `securities.csv` in Hebrew for TASE rows.
- **`desk/collect_email.py`** — polls a dedicated Gmail inbox over IMAP for
  `UNSEEN` mail, parses subject/sender/body (HTML via BeautifulSoup),
  best-effort tags each message to a security by sender/subject/body
  match, and inserts it. Never deletes or moves mail; marks a message
  `\Seen` only after it's been successfully stored, so a mid-run failure
  leaves it for retry. If `GMAIL_USER`/`GMAIL_APP_PASSWORD` aren't set, it
  exits cleanly as a no-op (useful for local dev without mail creds).

Run manually: `python -m desk.collect_news`, `python -m desk.collect_email`,
`python -m desk.collect_prices`.

## Prices (two tiers)

`desk/collect_prices.py` upserts one `quotes` row per watchlisted security
(union across users): last price, previous close, day change, and
MTD/QTD/YTD/12M returns. `securities.price_source` picks the tier:

- **`yfinance` (auto)** — batch daily history from Yahoo. The yfinance
  ticker is `securities.yahoo_symbol` when set, otherwise the symbol
  (+`.TA` for `market=TASE`). **`.TA` prices arrive in ILA (agorot) and are
  stored ÷100 as ILS** — `quotes.currency` is always post-conversion.
  Period anchors (close before month/quarter/year start, and ~12 months
  ago) are recomputed once per calendar day (`quotes.anchors_date`);
  in-between runs only refresh the last price and day change. Off-hours
  and weekends are safe — the latest close is simply re-reported (TASE
  trades Mon–Fri as of 2026, so no special calendar handling exists).
- **`manual`** — for securities with no free source (e.g. Sano 813014,
  Bio-Dvash 1082346). Enter price points by hand, in ILS:

  ```
  python -m desk.manual_price <sec_id> <YYYY-MM-DD> <close>
  ```

  Re-entering the same date updates the close. The collector takes the
  latest entry as the last price (`as_of` = its date, day change always
  NULL) and computes each period return from the nearest entry
  on-or-before that period's anchor date.

Status semantics (`quotes.status`): `ok` = priced this run; `no_data` =
nothing available yet (junk is never written); `stale` = the fetch failed
but an older good row was kept. A NULL period return with `status='ok'`
means history doesn't reach that far back (e.g. a recent IPO — the row
covers returns only *since its first trading date*, which the collector
logs); for manual securities it means no entry predates that anchor.

## Environment / secrets

See `.env.example`. Names (never commit real values):

| Variable | Purpose | Required |
|---|---|---|
| `DESK_DB_URL` | DB connection string | No (defaults to local SQLite) |
| `DESK_DEFAULT_USER` | Seeded username | No (defaults to `owner`) |
| `GMAIL_USER` | Dedicated inbox address | Only for email collection |
| `GMAIL_APP_PASSWORD` | Gmail app password (needs 2FA on the account) | Only for email collection |

In GitHub Actions these are repo/environment **secrets**, referenced by
`.github/workflows/collect.yml` — never hardcoded.

## Scheduled collection (GitHub Actions)

`.github/workflows/collect.yml` runs both collectors every 15 minutes
against the secrets above. It fails fast if `DESK_DB_URL` isn't set —
there's no local DB fallback in CI, since a hosted Postgres is required
for a scheduled job to persist anything meaningful between runs.

## Setup checklist (manual, outside this repo)

1. Create a hosted Postgres DB (e.g. Supabase or Neon free tier) and set
   its connection string as the `DESK_DB_URL` secret.
2. Create a dedicated Gmail inbox, enable 2FA, generate an app password,
   and set up a forwarding rule into it; set `GMAIL_USER`/`GMAIL_APP_PASSWORD`.
3. `gh` CLI is not installed on this machine — the GitHub repo + secrets
   were not created/pushed this phase; do so manually (see `TODO.md`).

## Phase 2 (next)

Pricing (MTD/YTD math, `.TA` ILA→ILS conversion) + a hosted, multi-user
Streamlit dashboard usable on mobile. See `TODO.md`.
