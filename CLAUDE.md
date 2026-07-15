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
- **Deployment: Vercel, settings in the dashboard — no `vercel.json`** (Root
  Directory is a project setting a config file cannot set; the Vite preset
  already gives `npm run build` → `dist`). Root Directory is **`web`** (the repo
  root has no package.json) — the only non-default. Full checklist:
  `web/DEPLOY.md`. `VITE_*` vars are inlined at **BUILD** time, so they must
  exist in Vercel *before* the first build; adding them later requires a
  redeploy. Auth is `signInWithPassword` only, so Supabase's Site/Redirect URLs
  aren't needed for login — but set them, or link-based emails (password reset)
  point at the `localhost:3000` default.
- Data reads use the Supabase JS client (`web/src/useWatchlist.js`) against the
  same Postgres the collectors write; UI stays **READ-only**. Prices are
  already ILS-converted by the collector — the UI never divides by 100 again.
- **Auth model — shared pool, personal watchlist.** `watchlist.user_id`
  references **our own `users` table** (integer id), NOT the Supabase Auth uid;
  **`users.auth_uid`** (uuid, nullable, unique) bridges the two. Every UI
  read/write resolves `auth.uid()` → `users.id` through it
  (`useWatchlist(authUser)`, which self-provisions a users row on first login,
  keyed on auth_uid). `auth_uid` is **nullable on purpose** — `seed.py` and
  `init_db`'s `DESK_DEFAULT_USER` create users by username with no auth account.
  Do **not** "simplify" this by making `watchlist.user_id` the auth uuid: it
  would break both of those (they map username → integer id) and require a
  destructive type change on a live FK'd column.
- **The watchlist is the ONLY personal table.** `securities`, `quotes`,
  `price_history`, `news`, `emails`, `filings`, `tase_securities` are the
  **shared pool** — readable by every authenticated user, and `securities`
  INSERT is deliberately open to authenticated (adding a security is a global
  act; the collectors then gather for everyone). What's personal is the
  *watchlist row*, which RLS locks to its owner
  (`user_id in (select id from users where auth_uid = auth.uid())` for
  SELECT/INSERT/DELETE — see `sql/6b-1_per_user_auth_rls.sql`). The UI's own
  filtering is convenience, **not** the boundary — RLS is.
- **Collectors are unaffected by auth** and must stay that way: they join
  `watchlist` on `sec_id` and never reference `user_id`, so they always operate
  on the **union of every user's watchlist**; they also connect as the table
  owner, which bypasses RLS.
- **RLS is the gotcha for every table the UI reads.** The collectors created
  these tables via raw SQL; the Supabase `anon` role reading them via PostgREST
  is subject to RLS. **`GRANT SELECT` does NOT bypass RLS** — with RLS enabled
  and no policy, reads return an **empty array with no error** (looks like "no
  data" but is a permission block). Each UI-read table needs a read policy:
  `create policy "anon read" on public.<table> for select to anon, authenticated using (true);`
  Applied so far: `users`, `securities`, `quotes`, `watchlist`. **Still needed:
  `news`, `emails`, `filings`** (verified they return 0 rows / no error).
- **News feed = 4 source types, 3 filters** (`web/src/useNews.js`,
  `web/src/News.jsx`): one time-sorted feed merging **web news** (`news`
  category `stock`/`macro`), **email** (`emails`), **MAYA filing** (`filings`
  source `maya`), **SEC filing** (`filings` source `sec`), each tagged with a
  source-type badge (outlet name / מייל / מאיה / SEC). Tabs: **המניות שלי** =
  all four types whose `sec_id` is in the user's watchlist; **מאקרו וסקירות** =
  `news` category `macro` + emails with `sec_id IS NULL`; **הכל** = the union.
- Data queries avoid PostgREST nested embeds (FK relationships aren't detected
  on the raw-created tables — embeds return null joins); use flat `.in()`
  queries merged in JS instead (see `useWatchlist.js`).
- **Search routing** (`web/src/useSearch.js`, `web/src/SearchBox.jsx`) — one box,
  three sources, routed by what was typed: **Hebrew or a bare digit-string →
  `tase_securities` queried DIRECTLY** (ilike on `name`, prefix match on
  `security_number`) — instant, local, no MAYA call per keystroke; **Latin
  ticker/name → the `search` Edge Function** via `supabase.functions.invoke`
  (which sends the anon key/JWT). Debounced 300ms with an out-of-order guard.
  **Never auto-picks** — always a candidate list with a market badge, because
  same-ticker collisions (SAP SE vs Saputo) are valid-but-different companies.
- **Add = SHALLOW insert + collector enrichment.** The browser writes only what
  the candidate already told it (`sec_id`/`symbol`/`name`/`market`, plus
  `yahoo_symbol` and `maya_company_id` where known) — it never calls yfinance or
  MAYA to resolve prices/companyIds. `securities` is inserted **ON CONFLICT DO
  NOTHING** (`ignoreDuplicates`) so an already-enriched row is never downgraded;
  then a `watchlist` row for the current user. A security with no `quotes` row
  renders as **"ממתין לנתונים"** (not a blank). **Caveat:** only US/GLOBAL adds
  self-enrich (collect_prices, ~15 min). A **TASE** add has no letter ticker
  (`tase_securities.symbol` is always NULL — no free number→ticker source, and
  `<number>.TA` 404s), so it is inserted `price_source='manual'` and stays
  pending until `python -m desk.onboard_cli resolve TASE <number> --add` runs —
  the cron does **not** run `maya_ids`/onboarding. See TODO 4b-3.
- **Remove = watchlist row ONLY.** Never delete the security or its
  news/emails/filings/quotes — those are **shared across users**, so deleting
  them would destroy another user's data.
- **Writes need RLS policies too** (same trap as reads): the UI's add/remove
  needs INSERT on `securities`, INSERT/DELETE on `watchlist`, **USAGE on
  `watchlist_id_seq`** (SERIAL pk — inserts fail without it), and a **read
  policy on `tase_securities`**. Scoped `to authenticated` (login required), not
  `anon`. They can't yet be scoped per-user — `watchlist.user_id` still points at
  our `users` table, not the Auth uid, so **any logged-in user can modify any
  watchlist row**; tighten when the auth-uid mapping lands.
- **Security detail page** (`web/src/Detail.jsx`, `Chart.jsx`,
  `usePriceHistory.js`) — full-screen, reached by clicking a watchlist row
  (`openSecId` state in App; one page, **no router**). The × calls
  `stopPropagation()` so removing never also navigates. The chart is a
  **hand-rolled SVG** — deliberately **no charting library** — with time
  left→right so the newest point sits on the right (SVG coords are absolute and
  RTL does not mirror them). The line is the **gold accent**: a chart is
  decorative, and `grn`/`red` stay reserved for returns. The full series is
  fetched **once** and the חודש/רבעון/שנה selector **slices it client-side** —
  never refetch per period. Prices come from `quotes.currency` /
  `price_history.close`, both already normalized — **never re-divide**.
  **Never draw a line we can't justify:** manual-tier securities show
  "מחיר ידני, נכון ל-<date>" with no chart, <5 points shows "אין מספיק היסטוריה",
  and a period slice with <2 points says so — a 2-point line implies a trend
  that isn't there.
- `web/src/FeedItem.jsx` holds the feed item + source badges, **shared** by
  News.jsx and Detail.jsx so the four source types can't drift apart. The detail
  feed (`useSecurityFeed(secId)` in useNews.js) filters **server-side** by
  sec_id and omits the security tag (redundant inside one security).
- Current state: **step 5b** — login, two-panel dashboard (watchlist right with
  search/add/remove, news feed left) and the full-screen security detail page.
  Next is polish + deploy.

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
- **`tase_securities`** (`desk/collect_tase_list.py`, daily
  `.github/workflows/tase_list.yml`) — a searchable catalogue of TASE stocks
  (security_number PK, Hebrew name, company_id, type, is_primary_stock) that is
  the **local source for instant Israeli search** in the UI (the UI queries this
  table directly rather than hitting MAYA per keystroke). Populated
  **browserlessly** — plain HTTPS GET, browser-like headers, **no `Origin`**
  (foreign Origin → Imperva 403), **no Playwright** (search API GETs aren't
  gated, per research/EDGE_SEARCH_FINDINGS.md). No one-shot dump exists, so it
  **sweeps the companyId range** (~100..2650), calling
  `companies/<id>/details` for each → the company's PRIMARY STOCK
  (`mainSecurityId`) + `securityType`; bond-only/deleted/no-stock are skipped
  (~557 stocks, complete equity coverage). The stored `name` is the **full
  registered name** (`longName`, e.g. `בנק לאומי לישראל בע"מ`) — it contains
  both the brand and words like `בנק`, so searching either matches (the short
  brand alone made banks unfindable by `בנק`). Refreshed **daily** (~10 min,
  ~2,500 paced requests); **resumable** — company_ids refreshed within
  `FRESH_HOURS` are skipped, so interrupted/same-day re-runs are cheap. Long
  tail grows via onboarding + live MAYA search. See TASE_LIST_FINDINGS.md.
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
- **`price_history`** (daily closes behind the detail-page chart) — written by
  `collect_prices` from the **SAME ~400d frame it already pulls** for the period
  anchors: **no extra yfinance calls, ever**. Persisted only on the daily anchor
  refresh (`CHART_DAYS=365` of it); the short intra-day runs skip it. Stored
  `close` is the **NORMALIZED major-currency** value — it reuses the same
  `scale` from `normalize_currency()` that produced `quotes.last_price`, so the
  latest history close equals the watchlist price exactly. **Never store raw
  sub-units** (agorot/pence) and never re-divide: the ÷100 has exactly one home.
  Manual-tier securities mirror their `manual_prices` points as-is (sparse is
  correct — **nothing is interpolated or invented**). Retention
  `RETENTION_DAYS=400` (~13 months), pruned every run. Bulk writes go through
  `db.upsert_many()` (executemany ON CONFLICT DO UPDATE) — use it, not a loop of
  `upsert()`, for series data; DO UPDATE also lets a later Yahoo adjustment
  correct a past close.
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
- **GLOBAL equities** (`market='GLOBAL'`): a third resolver via **Yahoo's
  public search** (`query1.finance.yahoo.com/v1/finance/search`), filtered to
  `quoteType=='EQUITY'`. Yahoo search is **not** a safe auto-resolver —
  same-ticker collisions return valid-but-wrong companies with clean prices
  (RS=Reliance Steel vs RELIANCE.NS; SAP.TO=Saputo vs SAP.DE), which the NaN
  guard can't catch — so global is **resolve-assisted**: `suggest()` surfaces
  candidates, the user picks the exact Yahoo symbol, `resolve('GLOBAL', sym)`
  validates it. **Never auto-pick.** Query routing: **Hebrew or a bare 6-9
  digit number → MAYA** (Yahoo 400s on Hebrew); plain Latin ticker/name →
  **US (SEC) + Yahoo global merged**, de-duped by bare symbol (US wins its
  GLOBAL twin). See research/GLOBAL_COVERAGE_FINDINGS.md +
  ONBOARDING_GLOBAL_VALIDATION.md.
- **Search proxy Edge Function** (`supabase/functions/search/index.ts`, Deno/TS)
  — the UI's live search for **Yahoo (global) + SEC (US) ONLY**. **Israel is
  deliberately not in it**: the UI queries the local `tase_securities` table
  directly (instant, no live gate). The proxy exists because the browser
  *cannot* call these upstreams: Yahoo sends **no CORS** headers and 429s
  without a `User-Agent`; SEC 403s without a **descriptive contact UA** — both
  headers a browser may not set. Rules: the caller's **`Origin` is never
  forwarded** upstream (request headers are built from scratch); the SEC
  `company_tickers.json` (~800 KB) is fetched **once and cached in module
  scope** (24h TTL + in-flight dedupe) — never per keystroke; results are
  merged/de-duped by full symbol (US wins its GLOBAL twin) and **never
  auto-picked** — always a list, per the collision policy in
  research/GLOBAL_COVERAGE_FINDINGS.md. Fail-soft: one dead upstream returns the
  other's results plus a `notes[]` entry, never a 500. **Ranking is
  intentionally NOT a copy of `onboarding.py`'s**: SEC hits are scored (exact
  ticker > query-starts-a-word > ticker prefix > loose substring) and the merge
  **interleaves** US/GLOBAL — plain concatenation + substring matching buried
  `SAP.DE` under `CHESAPEAKE` (which contains "sap"). CORS is an **allowlist**
  (localhost any port + `*.vercel.app`) — add the real app origin on deploy.
  **JWT verification stays ON** (callers pass the public anon key) — this is not
  an open proxy, and **no secrets belong in the function code**. Not a port of
  the Python engine — `desk/onboarding.py` remains the resolver/validator.
  Deploy: `npx supabase@latest functions deploy search` (CLI is **not
  installed**; use `npx`, and note `npm i -g supabase` is unsupported by design).
- **Sub-unit currency ÷100** lives in one place, `collect_prices.normalize_currency()`:
  `ILA→ILS`, `GBp→GBP`, `GBX→GBP` (agorot/pence), everything else unscaled.
  `currency_for()` round-trips the stored major currency back to the native
  sub-unit by suffix (`.TA`→ILA, `.L`→GBp) so re-runs keep converting.
  Onboarding only records the display currency; the actual ÷100 is the
  collector's job (never double-handled).
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
