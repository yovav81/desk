# DESK

Watchlist dashboard (US tickers + Israeli securities, stocks & bonds):
current/MTD/YTD performance + aggregated news (web + forwarded emails).
Filings (SEC/MAYA/MAGNA) arrive later via a read-only link to an existing
system — always out of scope for this project.

Will eventually be a hosted, multi-user service (employees, mobile-friendly).
Phase 0 = data source investigation (`research/FINDINGS.md`). Phase 1 =
data foundation: DB schema, securities mapping, news/email collectors.
Phase 2a = two-tier price collector (done). Next: 2b MAYA filings collector,
2c React UI. See `TODO.md`.

## Folder isolation

All work stays inside `C:\desk`. Never read or write `C:\invest`,
`C:\screener`, `C:\wealth`, `C:\nadlan` — unrelated projects on this machine.

## Frontend (`web/`) — product name "GOLD"

- The dashboard UI lives in `web/` (Vite + React, **JavaScript** not TS),
  fully separate from the Python collectors in `desk/` (which it never
  imports). Product/brand name is **"GOLD"**; the repo/folder stays "desk".
  Dark, Hebrew **RTL**, Heebo + IBM Plex Mono. Run: `cd web && npm run dev`.
- **Colors** are centralized in `web/src/theme.js` — never hardcode colors in
  components. The accent is **gold** (`acc` `#D4AF37`, with `accHover`/`accSoft`/
  `accDim`/`onAcc`), used decoratively (logo mark, primary button, focus rings,
  active tab/filter, thin accent lines). `grn`/`red` are **functional** —
  reserved for gains/losses on returns — and must never be reused as accents.
- **Auth is Supabase** (`web/src/supabaseClient.js`,
  `signInWithPassword`). `web/.env` (gitignored; names in `web/.env.example`)
  holds `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY`. The anon key is the
  **PUBLIC frontend key** — safe in the browser bundle. **Never** put
  `DESK_DB_URL` or any Supabase service/secret key in `web/`; those stay
  backend-only (the collectors' GitHub Actions secrets). The dashboard reads
  data **READ-only**; collectors remain the only writers.
- `design_reference/` is a **visual-only** mockup (Claude Design export) — read
  it for colors/spacing/layout, never wire its code as the app or modify it.
- Data reads use the Supabase JS client (`web/src/useWatchlist.js`) against the
  same Postgres the collectors write; UI stays **READ-only**. Prices are
  already ILS-converted by the collector — the UI never divides by 100 again.
- **Auth-uid ↔ users mapping is not wired yet:** `watchlist.user_id` points at
  our own `users` table, not the Supabase Auth uid. The watchlist currently
  reads the seeded **"owner"** user (`OWNER_USERNAME` in `useWatchlist.js`) as a
  stand-in — a TODO to map properly so each user sees their own list.
- **RLS/grants:** the collectors created these tables via raw SQL, so the
  Supabase `anon` role may not have SELECT. If reads fail with a
  permission/RLS error, either enable RLS with a read policy, e.g.
  `alter table securities enable row level security; create policy anon_read on
  securities for select to anon using (true);` (repeat for `quotes`,
  `watchlist`, `users`), or grant read directly:
  `grant select on securities, quotes, watchlist, users to anon;`.
- Current state: **step 2** — login + the live watchlist table
  (`web/src/Watchlist.jsx`). News panel, add/search, and detail page are later
  steps.

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
- `desk/collect_news.py`, `desk/collect_macro.py`, `desk/collect_email.py`,
  `desk/collect_prices.py`, `desk/collect_maya.py` — **cloud collectors,
  WRITE-only** against `DESK_DB_URL`. Meant to run unattended on a schedule
  (`.github/workflows/collect.yml`, every 15 min). The eventual dashboard is
  **READ-only** against the same DB — never merge write/collection logic
  into dashboard code.
- **News categories & macro** (`news.category` = `'stock'` | `'macro'`):
  `collect_news.py` writes per-security `'stock'` rows; `collect_macro.py`
  writes general-economy `'macro'` rows (`sec_id=NULL`) from Globes RSS
  section feeds (`MACRO_FEEDS`: iID=2 home/economy, iID=585 capital markets —
  Calcalist/Bizportal block direct RSS, don't fight it). Emails have **no**
  category column — the read-time rule is `sec_id NOT NULL` = stock,
  `sec_id NULL` = macro. The dashboard's three filters map to: **My stocks** =
  `category='stock'` ∩ the user's watchlist (+ their stock emails); **Macro &
  reviews** = `category='macro'` (+ unassigned emails); **All** = the union.
- **MAYA filings** (`desk/collect_maya.py`, `desk/maya_ids.py`,
  `desk/maya_client.py`) — company disclosure **announcements** (headline +
  date + document link) for watchlisted TASE securities. The pattern was
  **independently replicated** from a live browser session, documented in
  `research/MAYA_FINDINGS.md` — it does **not** read or link to any other
  project. MAYA has no login but sits behind an Imperva/Incapsula bot gate:
  `maya_client.harvest_cookies()` clears it once per run in headless Chromium
  (desktop UA, `he-IL`, automation flags masked), then the JSON API is hit
  with a plain `requests.Session`. **GET trap:** never send
  `Content-Type: application/json` on a GET (WAF 403); set it only on the POST
  feed. Fail-soft everywhere: if the gate isn't cleared (no Incapsula cookie)
  or a response shape changes, log and **exit 0** — never crash the workflow.
  Docs resolve at `https://mayafiles.tase.co.il/` + attachment path; human
  page at `maya.tase.co.il/reports/details/<id>`. **Poll gently** (public
  regulatory feed): one harvest/run, small `limit`, spaced calls.
- **MAYA companyId caching:** `securities.maya_company_id` is resolved once
  per TASE security by `python -m desk.maya_ids` via a **2-hop** lookup
  (security number → official name via `search/market`, name → companyId via
  `companies/autocomplete` `key`). Do **not** use the "drop last 3 digits"
  shortcut — it's wrong for small caps (Bio-Dvash 1082346 → 2093, not 1082).
  `collect_maya.py` skips (never crashes on) securities with a NULL
  `maya_company_id` and logs a hint to run `maya_ids`. Dedup guard:
  `filings` UNIQUE(`source`, `maya_id`) — sacred, like `news.url`.
- **Two-tier pricing** (`securities.price_source`): `yfinance` securities
  are batch-fetched by `collect_prices.py` (last price, day change,
  MTD/QTD/YTD/12M; period anchors recomputed once per calendar day via
  `quotes.anchors_date`); `manual` securities (no free source, e.g. Sano
  813014, Bio-Dvash 1082346) get prices entered by hand:
  `python -m desk.manual_price <sec_id> <YYYY-MM-DD> <close>` (ILS, not
  agorot; same-date re-entry updates the close). Both tiers upsert one
  `quotes` row per security via `db.upsert()`. Empty/all-NaN yfinance
  history never overwrites good data (`status` = `no_data`/`stale`).
- Yahoo symbol resolution: `securities.yahoo_symbol` override, else
  `symbol` + `.TA` for `market=TASE` (`securities.resolve_yahoo_symbol`).
- **Onboarding engine** (`desk/onboarding.py`, CLI `desk/onboard_cli.py`) —
  the backend core behind "add any security" (no UI; that's 2c-2). Three
  functions: `suggest(query)` (partial input → ranked, de-duped, multiple
  matches — never auto-picks), `resolve(market, identifier)` → `ResolvedSecurity`
  or `NotFound`, and `add_to_db(resolved)` (idempotent upsert into `securities`;
  never touches `watchlist`, never downgrades a good row — `yfinance`→`manual`
  is refused and set fields aren't clobbered with NULL). It **reuses**, never
  re-implements: the SEC ticker map (`company_tickers.json`) for US identity,
  MAYA search + the 2-hop companyId from `desk/maya_ids`, and the yfinance
  NaN guard (`collect_prices.closes_series`) for price existence.
  **Manual-fallback rule:** a ticker that yfinance can't price with real
  non-NaN closes (e.g. `SANO.TA`, `BDVSH.TA`) resolves to
  `price_source='manual'` — never a guessed price. **No-guess policy:** every
  network path is fail-soft; unresolvable input returns `NotFound` with a
  reason, never a fabricated symbol. yfinance rejects numeric `.TA`
  (`629014.TA` 404s), and there's no free number→ticker source, so TASE letter
  tickers come from the known mapping; unknown TASE securities fall back to
  manual. **Name → primary stock:** a company-name search resolves to the
  company's PRIMARY STOCK only, via MAYA's authoritative `mainSecurityId`
  (`api/v1/companies/<id>/details`) — `resolve_company_to_primary_stock()`.
  Bonds/other series are added by their exact security number; a company with
  no stock (bond-only issuer) is surfaced as NOT-RESOLVABLE-BY-NAME with a
  hint to enter a number — never a guessed series (see
  research/COMPANY_PRIMARY_FINDINGS.md).
- `data/securities.csv` — the security-number/symbol/name/type/market
  mapping. TASE has no scriptable export (WAF-blocked, see Phase 0
  findings); seeded via manual browser export or (future) TASE DataHub's
  free "Securities (Basic)" API product.

## Data model rules

- `watchlist` is **per-user** (FK to `users`). `securities`, `news`,
  `emails`, and `filings` are **shared across all users** — collectors always
  operate on the union of every user's watchlist, never a single user's.
- Collectors store **raw data only**: no LLM calls, no summarization, no
  scoring. `news.summary` stays NULL; nothing paraphrases article, email, or
  filing content (MAYA stores headline + date + doc link only, no financial
  field codes).
- `news.url`, `emails.message_id`, and `filings`(`source`, `maya_id`) are
  UNIQUE — the dedup guards that make collectors safe to re-run on a cron.
  Don't relax these constraints.

## Known pitfalls

- **yfinance `.TA` tickers report price in ILA (agorot), not ILS** — divide
  by 100. Handled in `desk/collect_prices.py` (stored prices are ILS;
  `quotes.currency` is always post-conversion, never `ILA`). Percent
  returns are scale-invariant, so only price levels need converting. Any
  future code touching raw yfinance `.TA` prices must apply the same rule.
- TASE bonds have no free API source (Yahoo doesn't carry them; TASE's own
  endpoints are WAF-blocked; DataHub's EOD bond product is paid, ~$100/mo).
  Still an open decision — see `TODO.md`.
- `gh` CLI is not installed on this machine; GitHub repo/secrets setup for
  this project has been manual/undone so far — don't assume a remote exists.

## Secrets

Read from environment only, never hardcoded: `DESK_DB_URL`,
`DESK_DEFAULT_USER`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`. Documented in
`.env.example` / `README.md`. In CI these are GitHub Actions secrets.
