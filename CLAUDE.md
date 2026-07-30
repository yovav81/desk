# DESK

Watchlist dashboard (US tickers + Israeli securities, stocks & bonds):
current/MTD/YTD performance + aggregated news (web + forwarded emails).
Filings (SEC/MAYA/MAGNA) arrive later via a read-only link to an existing
system — always out of scope for this project.

**LIVE, multi-user, at desk-henna.vercel.app** (Vercel auto-deploys `web/` on
push; Supabase = auth + Postgres + Edge Function). All collectors run in the
cloud: news/macro/email/enrich/prices + MAYA & SEC filings, dispatched by
**Supabase pg_cron** (the primary clock — GitHub `schedule:` is best-effort
fallback only). Phases 0–19 done (224 securities incl. 50 classification-only
ETFs; full collect green in ~10m); the app is an **installable PWA**. History
and open items in `TODO.md`.

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
  **Applied to every UI-read table** (users/securities/quotes/watchlist/news/
  emails/filings/price_history/tase_securities); `users`+`watchlist` are
  per-user via auth.uid() (sql/6b-1). Any NEW UI-read table needs its policy
  or it will silently show empty.
- **News feed = 4 source types, 3 filters** (`web/src/useNews.js`,
  `web/src/News.jsx`): one time-sorted feed merging **web news** (`news`
  category `stock`/`macro`), **email** (`emails`), **MAYA filing** (`filings`
  source `maya`), **SEC filing** (`filings` source `sec`), each tagged with a
  source-type badge (outlet name / מייל / מאיה / SEC). Tabs: **המניות שלי** =
  all four types whose `sec_id` is in the user's watchlist; **מאקרו וסקירות** =
  items with **no `sec_id`** (macro news + unassigned emails) **PLUS anything
  attributed to an ETF/fund** — a basket item is market reading, not company
  news, while a stock-attributed item belongs to that security's own feed
  (Phase 16E). `asset_type` comes from a **session-level `securities` fetch in
  App.jsx** (mount-once, never per item) because the 50 ETFs are on no
  watchlist; an unresolvable `sec_id` counts as NOT macro, so an unknown stock
  item can never leak in. **הכל** = the union.
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
  renders as **"ממתין לנתונים"** (not a blank). **All markets self-enrich:**
  US/GLOBAL via collect_prices directly; a **TASE** add lands
  `price_source='manual'` with `yahoo_symbol` NULL and is resolved by
  `desk/collect_enrich.py` (ISIN→Yahoo, runs in collect.yml before prices), so
  it gets its ticker + first quote in the same collector run.
- **Remove = watchlist row ONLY.** Never delete the security or its
  news/emails/filings/quotes — those are **shared across users**, so deleting
  them would destroy another user's data.
- **Writes need RLS policies too** (same trap as reads): the UI's add/remove
  needs INSERT on `securities`, INSERT/DELETE on `watchlist`, **USAGE on
  `watchlist_id_seq`** (SERIAL pk — inserts fail without it). `watchlist`
  policies are **per-user** via auth.uid()→users.id (sql/6b-1, verified live:
  a second user sees an empty watchlist); `watchlist` **UPDATE** (manual
  ordering, Phase 13B) via sql/006b — same ownership check; `securities`
  INSERT stays open to authenticated by design (shared pool).
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
  **Email rows expand in place** (accordion, multi-open, per-row state that
  survives the 3-min refresh): body + attachment metadata are **lazy-fetched on
  first expand** (flat queries — the list query never loads bodies) and cached
  for the session. Body renders as **plain text only** (pre-wrap, `dir="auto"`,
  `overflowWrap:anywhere` — nothing to sanitize, HTML was stripped at collect
  time). Attachment chips show the ORIGINAL Hebrew filename + size and mint a
  signed URL **per click** (`createSignedUrl(path, 60)`), opening the tab
  **synchronously-then-navigating** — `window.open` after an `await` gets
  popup-blocked (Safari). `storage_path` NULL = oversize, greyed with
  "קובץ גדול מדי — לא נשמר". URLs inside a body are made clickable by
  `linkifyText` (Phase 16B) — one split on an http(s) pattern, trailing
  punctuation and an unbalanced `)` left out of the link, each href through
  `safeUrl`. **Still no HTML rendering and no `dangerouslySetInnerHTML`**:
  every segment stays text React escapes; only the href is synthesized.
- **PWA (Phase 17B)** — installable on Android/iOS home screens and desktop.
  `web/public/manifest.webmanifest` ("GOLD news" / short "GOLD", standalone,
  portrait, `dir: rtl`, `lang: he`, colors from `theme.js`: bg `#0A1120`,
  accent `#D4AF37`). Icons come from ONE master, `web/public/icon.svg`,
  rasterized to 192/512/512-maskable/apple-touch-180 with the **already
  installed** Playwright Chromium (no new dependency, no hand-edited PNG —
  edit the SVG and re-export; maskable insets the mark to the inner 80%).
  iOS ignores manifest icons, hence the `apple-touch-icon` + `apple-mobile-*`
  metas in index.html. The service worker (`web/public/sw.js`, registered
  **production-only** — in dev it shadows Vite's module graph) is
  **shell-only**: cache-first for same-origin hashed `/assets/*` + icons under
  a versioned cache with an activate-time purge, **NETWORK-ONLY for everything
  else** — Supabase auth/REST/storage never touches a cache, and no
  authenticated data is ever stored. Navigations are **network-first** (cached
  `/` is an offline fallback only): index.html names hashed assets, so a cached
  shell would survive a deploy pointing at files that no longer exist.
  Phase 17C put the GOLD mark on the favicon; the Vite mark is gone.
- **Layout (Phase 7):** the panel split is draggable (desktop only) via
  `SplitDivider` — pointer capture + **RTL-safe ABSOLUTE math**
  (`width = rect.right − clientX`; never `movementX` deltas, whose signs are
  the classic inverted-drag bug), clamp 25–75%, keyboard accessible
  (`role=separator`; ArrowRight enlarges the watchlist). Session-state only —
  resets on reload by design. The watchlist table lives in **one shared
  x-scroll container** (header INSIDE it — headers and rows can never scroll
  apart again); the name cell is sticky at `insetInlineStart: 0` (= RIGHT in
  RTL), the ✕ at the opposite edge, with edge shadows only while scrolled.
- **Mobile (≤760px, the mockup's breakpoint):** `useIsMobile` (matchMedia
  change-events only) branches ONCE at the top of Dashboard's return — the
  desktop tree renders exactly as before. Tab switcher (רשימת מעקב/חדשות)
  toggles `display` on always-mounted panels, so tab flips never refetch;
  watchlist becomes `SecurityCard`s. `SplitDivider` exists only in the desktop
  tree. `viewport-fit=cover` in index.html makes the safe-area padding real on
  notched phones. Crossing the breakpoint keeps all Dashboard-level state; the
  panels remount → one refetch per rotation (accepted).
- **Freshness (Phase 5):** feed tags show `securities.name` (fallback
  `symbol || sec_id`); auto-refresh = refetch on `visibilitychange` + a 3-min
  interval, **paused while the tab is hidden** (one timer in App drives both
  data hooks via `refreshTick` — never add a second timer). The news header
  shows **"הפריט האחרון"** from `max(published_at)` — deliberately NOT "when
  the browser fetched" (that would read "עכשיו" forever and lie). Phase 17A
  added a manual refresh button + "עודכן: לפני X" (re-runs the SAME `load()`;
  the auto-refresh timer is untouched — still exactly one timer).
- **Watchlist UX (Phase 13):** column sorting on all 7 columns incl. daily
  (numeric first-click desc, name asc via `localeCompare('he')` on
  `displayName` — sorts what's displayed; NULLs always last; manual-tier
  daily sorts as NULL because the cell renders '—'; ▲/▼ rides the text flow
  so the sticky column is untouched); live in-list filter (name/symbol
  substring, ✕ + Escape clear); **persistent per-user manual order** —
  `watchlist.position` (sql/006 + UPDATE policy sql/006b, both applied) is
  THE default order ("הסדר שלי"); header sorts overlay it in-memory, search
  filters whichever order is active, adds append at max+1. Reorder mode
  ("סידור") clears sort+filter (moves on a sorted/filtered view are
  ambiguous) and gives each row a **drag handle** (pointer capture,
  direct-DOM transforms — no per-move re-render, edge auto-scroll, drop
  commits once) + **send-to-top ⤒**; ArrowUp/Down on the focused handle is
  the keyboard path. Persistence = ONE debounced (~1.5s) batched upsert of
  changed positions; error → toast + revert to server state. Mobile: same
  filter + a compact sort select; cards get the same reorder handles.
- Current state: **deployed and live, desktop + mobile + installable PWA** —
  login + signup
  approval gate, resizable two-panel dashboard with search/add/remove,
  sticky-column watchlist with sorting/filtering and per-user drag ordering,
  detail page + chart, auto-refresh, mobile tabs with cards, and in-place
  email viewing with attachments. Remaining work is in TODO's Open items.

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
  `desk/collect_enrich.py`, `desk/collect_prices.py`, `desk/collect_maya.py`,
  `desk/collect_sec.py` — **cloud collectors, WRITE-only** against
  `DESK_DB_URL`. The dashboard is **READ-only** against the same DB — never
  merge write/collection logic into dashboard code.
- **Workflows layout:** `.github/workflows/collect.yml` = news → macro →
  email → **enrich** → prices (enrich runs BEFORE prices so a just-added TASE
  security gets ticker + first quote in the same run);
  `filings.yml` = MAYA + SEC (the time-sensitive lane; SEC last because it's
  the only step with a hard-fail config mode); `tase_list.yml` = daily TASE
  registry sweep. House pattern: secrets at job level, no `continue-on-error`,
  every collector internally fail-soft. **Hardening (12A-H/H2):** per-step
  `timeout-minutes` (news 15 — measured at 162-security scale; macro/email 5;
  enrich/prices 10); enrich+prices run `if: success() || failure()` so a hung
  email can't starve prices (not on cancel); the Healthchecks ping stays
  success-only and LAST; `PYTHONUNBUFFERED: "1"` at job level (a timeout-killed
  step must not lose its buffered tail); `IMAP4_SSL(timeout=60)` + per-phase
  EMAIL log lines (connect/login/select/search/fetch i/n/storage upload) so
  the intermittent email hang pinpoints itself. Known gaps: DNS resolution
  precedes the socket timeout, and the 60s IMAP timeout is per-read.
  **Least privilege (15D):** every workflow declares `permissions: contents:
  read` and pins each action to a full commit SHA — see Security posture.
- **Scheduling — pg_cron is the clock, GitHub `schedule:` is fallback.**
  GitHub schedule events were MEASURED arriving 74–180 min apart despite */15
  and */5 crons (documented best-effort/droppable; paying doesn't help; a
  lightweight dedicated workflow didn't help either). The fix: **Supabase
  pg_cron + pg_net** POST `workflow_dispatch` to GitHub — jobs
  `desk-dispatch-filings` ('*/5 * * * *') and `desk-dispatch-collect`
  ('2,17,32,47 * * * *') — with a fine-grained PAT (Actions:write, this repo
  only, expires ~2026-10-14) stored in **Supabase Vault** as
  `gh_dispatch_token`. Measured: dispatch→run-start <1 min; end-to-end
  filing→dashboard ~7 min. The yml `schedule:` blocks stay as a free lazy
  fallback — dedup absorbs overlaps. **Ops gotchas:** pg_net is async —
  `cron.job_run_details` 'succeeded' only means the POST was queued; the real
  GitHub status code is in `net._http_response` (that's how a placeholder-token
  401 was caught). Vault `create_secret` stores whatever string it's given —
  verify by length/prefix after storing, never assume. The repo is **PUBLIC**
  (full-history secret scan first: research/PUBLIC_REPO_SECRET_SCAN.md — zero
  credentials ever committed), so Actions minutes are free.
- **News categories & macro** (`news.category` = `'stock'` | `'macro'`):
  `collect_news.py` writes per-security `'stock'` rows, **routed by market**
  (Phase 12): **US → Finnhub** company-news (`FINNHUB_API_KEY`; missing key →
  one WARNING + Google News fallback), **GLOBAL → GDELT** (next bullet),
  **TASE → Google News RSS**. `collect_macro.py` writes general-economy
  `'macro'` rows (`sec_id=NULL`) from `MACRO_FEEDS` — four feeds (Phase 18):
  Globes iID=2 home/economy, **ynet_economy** RSS (globes_markets iID=585 went
  silent 2026-07-14 and is retired), **google_world_macro** (Google News SEARCH,
  en-US: central banks / interest rates / inflation / global economy) and
  **google_il_macro** (he-IL, `site:calcalist.co.il OR site:themarker.com` +
  finance-specific terms — those two outlets block direct RSS, so search is the
  way in; broad terms like כלכלה let their sports desk through, measured, so
  the terms stay narrow: probe was 10/10 on-topic, 5/5 outlet split). The
  **BUSINESS topic channel is deliberately not used** — it is dominated by
  single-company stories, which is per-security news, not macro.
  **gdelt_macro** stays behind its hourly gate as a bonus, not a dependency.
  Per-feed summary lines; read=0 logs "FEED SILENT" (a dead feed must
  scream). Macro cost ~1m22s with four feeds (was ~9s with two) against a
  5-minute step timeout — check that before adding a fifth. Emails have **no**
  category column — the read-time rule is `sec_id NOT NULL` = stock,
  `sec_id NULL` = macro. The dashboard's three filters map to: **My stocks** =
  `category='stock'` ∩ the user's watchlist (+ their stock emails); **Macro &
  reviews** = `category='macro'` (+ unassigned emails); **All** = the union.
- **GDELT (GLOBAL news + gdelt_macro)** — keyless DOC API, batched **6 names
  per call** (`("N1" OR "N2" …) sourcelang:english`, timespan=3d,
  maxrecords=75), attributed by a deterministic relevance guard: ALL name
  tokens (len≥3) must appear in the title, else `skipped_offtopic`
  (multi-match → all passing securities). CI runner IPs are
  **intermittently 429-throttled** (shared IPs, multi-minute per-IP
  cooldowns — measured; not a permanent block), so: 20s per-call timeout, a
  **circuit breaker** (3 consecutive 429s → skip remaining GLOBAL batches
  this run, one warning), and an **hourly gate** — GDELT runs only when UTC
  minute<15 (= the :02 dispatch run; `GDELT_FORCE=1` overrides).
  timespan=3d means one successful hourly attempt loses nothing. Don't "fix"
  a 429 by retrying in-run.
- **Near-duplicate title suppression** (write-time, both news collectors):
  the same story arrives from several sources with different URLs, so beyond
  UNIQUE(url), `norm_tokens`/`is_similar` (ONE definition in collect_news,
  imported by collect_macro): Jaccard ≥ 0.75 AND ≥ 4 shared tokens vs the
  last 72h of titles in the same group (per sec_id; macro = the NULL group;
  ONE query per run, inserted titles join in-memory so intra-run dupes are
  caught). Skipped-not-deleted; counter `skipped_similar` (first full-scale
  run caught 1,083).
- **News staleness gate** (`collect_news.is_stale`, `STALE_DAYS=7`, imported by
  collect_macro — ONE definition): Google News RSS resurfaces archive items
  (**73% of a measured run was stale**); anything PROVABLY older than 7 days at
  ingest is skipped and counted, never inserted. Missing dates are NOT stale
  (act only on proof; stored with published_at NULL as always). Ingest-only —
  existing rows untouched. **Log vocabulary rule, learned twice:** counters are
  named literally — `read=/inserted=/duplicate=/skipped_stale=/
  skipped_similar=/skipped_offtopic=`; never name a read-count `new=`.
- **`inserted=` must come from the DB, not from intent (Phases 18-FIX2/3).**
  Both news collectors derive it from `.returning(news.c.id)` +
  `conn.execute(stmt).first() is not None`, and `duplicate = attempted -
  inserted` (`attempted` = rows that reached the insert). **Do NOT use
  `result.rowcount` on an ON CONFLICT DO NOTHING insert** — MEASURED
  2026-07-30: two macro runs logged `inserted=31` while the DB gained **zero**
  rows, and `duplicate=` was structurally always 0. Prod after the fix:
  inserted 50 → 4, duplicate 0 → 50. **Historical `inserted=` numbers in old
  logs are inflated** — don't compare across the fix.
- **Email attribution** (`collect_email.attribute_email`) — a strict confidence
  ladder. Two **EXCLUSIVE Bloomberg tiers** sit on top (Phases 16D/16F/16G): if
  a subject has a Bloomberg shape it is decided there and **never** reaches the
  symbol/name tiers, because alert text ("Volume Since Open", "Target Px
  increased") is common English that collides with company names — that is how
  `LSEG LN` once became LPRO (Open Lending).
  1. **`bbg`** — `"<TICKER> <CC> Equity …"`. `BBG_SUFFIX` maps Bloomberg's own
     venue codes to our symbols: US/UN/UW/UQ/UR/UA/UP → bare, FP `.PA`,
     TT `.TW`, SW `.SW`, JT/JP `.T`, KP `.KS`, GY `.DE`, LN `.L`, IM `.MI`,
     SM `.MC`, NA `.AS`, BB `.BR`, SS `.ST`, HK `.HK`, AU `.AX`, CN `.TO`,
     SJ `.JO`. Mapped + tracked → attribute; mapped + untracked → **SKIP the
     mail** (`skipped_untracked_stock`) — a per-stock alert is never macro;
     **UNMAPPED code → KEEP with sec_id NULL + WARNING + `bbg_unmapped_code`**.
     No bare-ticker fallback: `AAPL CN` is a foreign twin, and dropping mail
     requires certainty an unknown code doesn't give. Add the code to the map
     and the sweep claims those rows for free.
  2. **`bbgname`** — `"<COMPANY NAME>: <action>"`, no ticker. Normalize both
     sides (uppercase, punctuation → space, drop trailing corporate suffixes),
     then: ≥8 significant chars → **prefix match in either direction**
     (Bloomberg truncates names to ~28 chars: `TAIWAN SEMICONDUCTOR MANUFAC`);
     <8 → **exact equality only**, so APPLE/VISA stay attributable while
     APPLEBEES can't claim APPLE. Requires an **all-caps, multi-word** name —
     without that guard `Morning Brief:` (a newsletter) and `AAPL: earnings`
     (a ticker subject) would be swallowed and deleted. No match → skip.
  Then the original ladder: security number as a standalone token >
  **whole-word** ticker (len≥2 — **single-letter symbols are structurally
  excluded**: Citigroup's 'C' substring-matched the 'c' in every ".com" sender
  and tagged ALL email as C) > distinctive Hebrew/English name tokens
  (gershayim-normalized `בע"מ`→`בעמ`; NOISE_TOKENS strips בנק/מערכות/Ltd/…) >
  **NULL = macro**. Multi-match at any tier → NULL + warning — wrong
  attribution is worse than none. Never match against sender text.
- **Symbol-tier guards (Phase 19).** `WORD_LIKE_SYMBOLS` — 26 tickers that are
  also ordinary English words (IT, NOW, COST, MA, TW, FIX, FAST, GE, BA, V, C,
  ICE, …) — **never match from prose**; a Google Alert about הפניקס was
  confidently attributed to ICE, and the multi-match guard can't help when only
  one such symbol hits. It is a blocklist of **symbols as text needles**, not of
  securities: each stays reachable by number, by name, and by both Bloomberg
  tiers. Suppressions log `SYMBOL_BLOCKED`. Additionally, a symbol only counts
  as aboutness in the **SUBJECT or the first 500 body chars**
  (`SYMBOL_BODY_CHARS`) — a ticker buried 3,000 chars into a digest is a
  mention. The secnum and name tiers still scan the whole body.
- **The NULL-sweep needs a marker or it starves** (`reattribute_nulls`,
  sql/010). It re-runs the ladder over unattributed mail so a security (or a
  new tier) added later can still claim it. It used to select
  `sec_id IS NULL ORDER BY id DESC LIMIT 200` — a row that failed kept its id
  and re-entered the identical top-200 window every run, so with 1,273 NULL
  rows older mail was **never read**. Now the predicate is
  `sec_id IS NULL AND sweep_checked_at IS NULL` and **every examined row is
  marked**, hit or miss; the log ends with `sweep_remaining=N`. A non-NULL
  sec_id is still NEVER rewritten. After shipping a new tier, re-open the
  backlog with `update emails set sweep_checked_at = null where sec_id is null`
  (the `SWEEP_RESET_HINT` constant — never auto-run).
- **Email attachments** (`collect_email` + `desk/email_backfill.py`, sql/004) —
  files live in the **PRIVATE Storage bucket `email-attachments`** (manual
  dashboard creation), reachable only via signed URLs (storage.objects policy:
  `authenticated` SELECT); metadata rows in `email_attachments` (anon-read,
  like the feed). Upload via **Storage REST with `requests`** — no supabase-py
  dependency. **Object keys must be ASCII**: Storage 400s non-ASCII keys
  (production-measured; supabase/storage#133), so keys are DERIVED —
  `{email_id}/{sha1(name)[:16]}{.ext}` — and the original Hebrew filename
  lives in the metadata row for display. 20MB cap (oversize → metadata-only
  row, storage_path NULL); **14-day retention sweep** (free tier = 1GB;
  ~60MB/day fills it in ~17 days) deletes objects + metadata rows only —
  emails/body_text are never touched. Failed uploads write nothing (retry =
  the backfill CLI, dry-run default, keyed on message_id).
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
- **SEC filings** (`desk/collect_sec.py`, `desk/sec_ids.py`) — per-company
  `data.sec.gov/submissions/CIK##########.json` for watchlisted `market='US'`
  securities with a cached `securities.cik` (backfilled by
  `python -m desk.sec_ids --commit`; `cik_to_path()` is THE one zero-padding
  site — never re-pad). Requires a **descriptive User-Agent** (`SEC_USER_AGENT`
  env — generic/absent UA → 403). **Form allowlist** (10-K/10-Q/8-K/DEF 14A/
  20-F/6-K + their /A amendments — unfiltered, the feed is ~59% Form 4 noise;
  foreign issuers like SAP file 20-F/6-K, never 10-K), Hebrew titles composed
  from a static map ("דוח שנתי (10-K)"), 90-day window. Dedup:
  `filings` UNIQUE(`source`, `accession_no`) (sql/002; `maya_id` is NULL on sec
  rows, `accession_no` NULL on maya rows — NULLs are distinct, the guards never
  interfere). CLI defaults to **dry-run**; CI passes `--commit`.
- **TASE ticker enrichment** (`desk/collect_enrich.py`) — resolves the letter
  ticker for UI-added TASE securities (`yahoo_symbol IS NULL`), the one datum
  no other source provides. Method (validated n=50, 92% match, **zero
  wrong-company** — research/TASE_ENRICHMENT_FINDINGS.md): **constructed ISIN**
  (`"IL" + zfill(9)(number) + Luhn`) → Yahoo search → **mandatory TLV gate**
  (`is_tlv_listing()`: Tel Aviv exchange AND `.TA` suffix — Camtek's ISIN
  returns only its NASDAQ line, which would store USD prices on an ILS row).
  Identity is structural (the ISIN *contains* the security number — no name
  collisions); Yahoo's name is logged for eyeballing. `price_source` flips to
  `yfinance` only after the NaN guard confirms real closes; rows with
  hand-entered `manual_prices` are NEVER flipped (deliberate migration only).
  NO-HIT/non-TLV/no-prices → stays as-is, logged — **never guess**.
  `MAX_PER_RUN=25` caps Yahoo load; dry-run default, `--commit` in CI.
- **`securities.asset_type` (Phase 16C)** — populated from yfinance
  `quoteType` by a second pass in `collect_enrich` (`fast_info.quote_type`, the
  cheap read; `.info` pulls the whole quoteSummary for the same string).
  EQUITY→`stock`, ETF→`etf`, MUTUALFUND→`fund`, INDEX→`index`, anything else
  stored raw-lowercased; unresolvable → row untouched and counted, never
  fabricated. Live: **etf 50, stock 173, fund 1**. Idempotency needs a marker —
  a row that resolves to `stock` is indistinguishable from an unchecked one, so
  **`asset_type_checked_at`** (sql/009) is stamped on EVERY completed check and
  the selection is `checked_at IS NULL`; the set drains monotonically instead
  of re-fetching every equity forever. The 50 ETFs (SPY QQQ VOO XLK XLF XLE
  XLV SMH TLT GLD …) are in `securities` but on **no watchlist** — they exist
  to classify mail, not to be tracked.
- **Timestamps: sources lie about timezones — convert in ONE place per
  collector.** SEC `acceptanceDateTime` says `Z` but is **US Eastern wall
  clock** (measured against the ATOM feed); `collect_sec._parse_published`
  strips the false label and converts via `zoneinfo America/New_York`. MAYA
  `publishDate` is **naive Israel local**; `collect_maya._parse_published`
  attaches `Asia/Jerusalem` and converts. Always zoneinfo, **never a fixed
  offset** (DST flips both twice a year). `published_at` in the DB is genuine
  UTC. Historical rows: SEC backfilled via sql/003 (guarded by
  `applied_migrations`); MAYA rows were deleted + re-collected after the
  sql/003 MAYA backfill went wrong — see Lessons.
- **Two-tier pricing** (`securities.price_source`): `yfinance` securities
  are batch-fetched by `collect_prices.py` (last price, day change,
  MTD/QTD/YTD/12M; period anchors recomputed once per calendar day via
  `quotes.anchors_date`); `manual` securities get prices entered by hand:
  `python -m desk.manual_price <sec_id> <YYYY-MM-DD> <close>` (ILS, not
  agorot; same-date re-entry updates the close). Both tiers upsert one
  `quotes` row per security via `db.upsert()`. Empty/all-NaN yfinance
  history never overwrites good data (`status` = `no_data`/`stale`).
  **Unit-jump guard (Phase 11):** raw Yahoo `.TA` series can arrive
  MIXED-unit — agorot for one segment, ILS for the rest (a single ~×100 step
  at 2026-05-18 made NXSN y12 17390.7% / ytd 11057.7%; corrected to 74.9% /
  11.58%). `normalize_unit_jumps()` in collect_prices detects a
  consecutive-close ratio ≥50 or ≤0.02 and rescales the pre-jump segment;
  MULTIPLE jumps → returns skipped for that security (quote fields still
  written) — never guessed. 8 corrupt tail rows (2 NXSN + 6 DANH) were
  deleted from price_history.
  **Note:** Sano 813014 (`SANO1.TA`) and Bio-Dvash 1082346 (`BHNY.TA`) DO have
  Yahoo listings — the Phase 0 "no free source" conclusion was built on
  guessed tickers and is overturned. They stay on the manual tier with their
  hand-entered prices until a **deliberate** migration (open item).
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
  (`629014.TA` 404s); TASE letter tickers come from the DB row or, for unknown
  securities, from the **ISIN enrichment collector** (`collect_enrich`, above) —
  onboarding itself still never derives one, and unresolved TASE securities
  fall back to manual. **Name → primary stock:** a company-name search resolves to the
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

## Security posture (Phase 15 — audit in research/SECURITY_AUDIT_FINDINGS.md)

Audit result: CRITICAL 0, HIGH 2, MEDIUM 5, LOW 6 — every HIGH+MEDIUM
technical item is CLOSED. What holds now:

- **Grants (sql/007):** the blanket GRANTs are revoked. `anon` has **ZERO**
  table privileges. `authenticated` keeps SELECT everywhere (RLS does the
  filtering) and writes only where the app needs them — watchlist
  INSERT/UPDATE/DELETE, `securities` INSERT, `users` INSERT, `profiles`
  UPDATE. `ALTER DEFAULT PRIVILEGES` was fixed too, so a NEW table is not born
  world-readable — but it still needs its own read policy (see the RLS gotcha).
- **SECURITY DEFINER functions (sql/008):** EXECUTE revoked from anon +
  authenticated on all five (each confirmed `prosecdef=true`, so RLS is
  unaffected).
- **Dependencies (15A):** `requirements.txt` is fully pinned — 30 exact `==`
  pins (7 direct + 23 transitive) taken from a real `pip freeze`; `requests` is
  now an explicit direct dep. Hash pinning deferred.
- **Workflows (15D):** all three carry `permissions: contents: read`, and every
  action is **SHA-pinned** (checkout `11d5960` v4.4.0, setup-python `a26af69`
  v5.6.0, cache `0057852` v4.3.0) — tags are mutable, and these jobs hold
  `SUPABASE_SERVICE_ROLE_KEY`. Bumping a version means bumping a SHA.
- **URL scheme guard (15C):** `safeUrl()` in `web/src/FeedItem.jsx` — every
  href/navigation built from stored data must match `^https?://` or it renders
  as plain text. Blocks `javascript:`/`data:` from a poisoned feed.
- **Still open, tagged "before public launch"** (not blocking the current
  invite-only group): 15F password-reset flow, 15G Edge Function `is_approved`
  check, signup hardening (min length 8+, CAPTCHA, verify prod rate limits).

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
- `gh` CLI is not installed on this machine. The repo is
  github.com/yovav81/desk — **PUBLIC** (secret-scanned first); GitHub setup is
  done in the browser, not via `gh`.
- **pg_net is async:** `cron.job_run_details` saying 'succeeded' only means
  the HTTP call was queued — the real GitHub status lives in
  `net._http_response.status_code`. Check it; a 401 hides behind 'succeeded'.
- **Vault stores whatever string you give it** — after `create_secret`/
  `update_secret`, verify by length/prefix; a placeholder saved by mistake
  looks identical to a real token until GitHub says 401.

## Lessons (paid for in production — keep them)

- **An implausible number can be real.** "MAYA doesn't publish at 2am" felt
  like proof of a timezone bug — but MAYA publishes Form 4s at 23:00+. The
  implausibility argument was the wrong lens; only comparing against ground
  truth (the website) settles a timestamp.
- **A code fix and a data backfill are separate decisions.** The MAYA code fix
  was correct AND the backfill corrupted the data. Approve them separately;
  verify the rows' actual state before shifting anything.
- **When the corruption mechanism isn't understood, re-collect from source** —
  don't compute a repair on top of a model you can't confirm. Deleting and
  re-collecting the maya rows fixed in minutes what two computed repairs
  argued about for a day.
- **`fetched_at` semantics are UNKNOWN** (overwritten-per-run vs
  ON-CONFLICT-preserved — the evidence was destroyed by the re-collect).
  Build **nothing** on it until it's resolved (open item).
- **A documented conclusion built on a guess is still a guess.** Phase 0
  recorded "Sano/Bio-Dvash have no free source" after probing *guessed*
  tickers; the real listings (SANO1.TA/BHNY.TA) existed all along. Mark
  MEASURED vs INFERRED honestly — an INFERRED claim marked MEASURED cost us
  160 corrupted rows once already.
- **GitHub `schedule:` is best-effort and can be hours late** — measured
  74–180 min gaps on a correct cron. If timing matters, dispatch externally
  (pg_cron → workflow_dispatch) and keep the cron only as fallback.
- **The C-substring class of bug: never substring-match short identifiers
  against free text.** A 1-letter ticker as a match needle tagged every email
  in the inbox as Citigroup ('c' ∈ every ".com" sender). Whole-word matching
  with a minimum length is the floor; single-letter symbols are excluded from
  text matching structurally, not case-by-case.
- **A live API rejects what an offline harness passes.** The attachment
  pipeline passed every offline test, then the FIRST real Hebrew filename got
  HTTP 400 from Storage (non-ASCII object keys). The first production run is
  part of verification, not a formality — watch it.
- **A counter that reports intent is a lie, and a comment asserting otherwise
  is worse.** `inserted=` was counted from items that PASSED THE GATES, so
  ON CONFLICT DO NOTHING swallowed rows silently and `duplicate=` was always 0
  — two macro runs logged `inserted=31` while the DB gained nothing. A code
  comment had ASSERTED that `rowcount` was reliable there; the assertion had
  never been measured. Derive counts from what the DB RETURNS, and treat an
  unmeasured assertion in a comment as unverified no matter how confident it
  reads.
- **A window that never advances processes the same rows forever.** The
  null-sweep re-read the newest 200 unattributed emails every run; failures
  kept their ids, so 1,073 older rows were unreachable — including mail a newer
  tier would have matched. Any "retry the failures" loop needs a marker column
  (`sweep_checked_at`, `asset_type_checked_at`) or it starves its own tail.
- **"Already built" in docs can mean a mockup.** The mobile cards "already
  built" claim referred to design_reference markup, not code — the third
  overturned documented claim in one week (Sano tickers, the enrichment TODO,
  mobile). Verify docs against code before building on them.

## Secrets

Read from environment only, never hardcoded: `DESK_DB_URL`,
`DESK_DEFAULT_USER`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `SEC_USER_AGENT`
(descriptive UA with a contact — SEC 403s without it), `FINNHUB_API_KEY`
(US company-news; the collect.yml news step only), `SUPABASE_URL`, and
`SUPABASE_SERVICE_ROLE_KEY` (**bypasses RLS — GitHub Actions Secrets ONLY,
never in Vault, never in web/, never logged; errors name the variable, never
the value**). Documented in `.env.example` / `README.md`. In CI these are
GitHub Actions secrets — still private on the public repo. The **GitHub dispatch PAT** (fine-grained,
Actions:write, this repo only, expires ~2026-10-14) lives ONLY in **Supabase
Vault** as `gh_dispatch_token` — never in the repo, never in Actions secrets;
rotate via Vault `update_secret` (open item).
