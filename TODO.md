# DESK — TODO

## Phase 0: Data source investigation — DONE (2026-07-08)
- [x] TASE securities list — automated download blocked by WAF; manual/DataHub path documented
- [x] yfinance TASE stocks (ILA/agorot pitfall confirmed)
- [x] TASE bonds via yfinance — ruled out; TASE JSON endpoints — ruled out (WAF/ToS)
- [x] TASE DataHub products + 2025 price list reviewed (free: Securities Basic; paid: EOD quotes)
- [x] US quotes via yfinance
- [x] News: Globes RSS + Google News RSS verified; Calcalist/Bizportal blocked; yfinance news OK
- [x] FMP stock-news — docs-only, paid tier, skipped
- [x] Email ingestion research — recommend Gmail IMAP + app password
- Findings: research/FINDINGS.md

## Phase 1: Schema + collectors foundation — DONE (2026-07-08)
- [x] Postgres-ready DB schema (SQLAlchemy Core, portable SQL) — desk/db.py
- [x] Securities CSV mapping + lookup — desk/securities.py, data/securities.csv
- [x] News collector (Google News RSS, per watchlisted security, union across
      users, dedup on news.url) — desk/collect_news.py — verified against
      live RSS (210 rows inserted, re-run produced 0 new)
- [x] Email collector (Gmail IMAP, dedup on emails.message_id, best-effort
      sec_id tagging, never deletes/moves mail) — desk/collect_email.py —
      structurally verified (clean no-op without creds); not yet run against
      a real inbox (no credentials created this phase)
- [x] GitHub Actions workflow (every 15 min, fails fast without DESK_DB_URL)
- [x] README.md, requirements.txt, .env.example
- Manual steps still needed (not done — no signups performed):
  - [ ] Create hosted Postgres (Supabase/Neon free tier) → set DESK_DB_URL secret
  - [ ] Create GitHub repo (`gh` CLI not installed here) + push + add secrets
  - [ ] Create dedicated Gmail inbox + enable 2FA + app password + forwarding
        rule → set GMAIL_USER / GMAIL_APP_PASSWORD secrets
  - [ ] Seed data/securities.csv for real (manual TASE browser export)

## Phase 2a: two-tier price collector — DONE (2026-07-13)
- [x] Schema: `quotes` (one upserted row per security) + `manual_prices`
      (UNIQUE sec_id+price_date); `securities.yahoo_symbol` override column
      with idempotent ALTER migration in init_db — desk/db.py
- [x] desk/collect_prices.py — auto tier (yfinance batch fetch, ILA→ILS ÷100,
      daily period anchors for MTD/QTD/YTD/12M, no-junk validation:
      no_data/stale statuses) + manual tier (returns from manual_prices
      entries, day_change NULL) — verified live: TEVA ~98 ILS, BGRA (recent
      IPO) NULL YTD/12M, re-run produced no duplicate rows
- [x] Manual price CLI: `python -m desk.manual_price <sec_id> <YYYY-MM-DD> <close>`
      (ON CONFLICT updates close) — verified incl. same-date re-entry
- [x] Seed: Sano 813014 + Bio-Dvash 1082346 (manual), Bagira 1242882 +
      Dan Hotels 822015 (yfinance) on owner's watchlist
- [x] collect.yml: prices step added after news/email

## Phase 2b: MAYA filings collector — DONE (2026-07-13)
- [x] Pre-check (research/MAYA_FINDINGS.md) + Actions harvest probe PASSed —
      MAYA runs in the cloud like the other collectors (probe now deleted)
- [x] Schema: `securities.maya_company_id` (idempotent ALTER) + `filings`
      table (UNIQUE(source, maya_id) dedup guard, published_at index) — desk/db.py
- [x] desk/maya_client.py — shared harvest (headless Chromium, he-IL, automation
      masked), gate-cleared check, requests.Session builder, doc-url helper
- [x] desk/maya_ids.py — 2-hop companyId resolution (number→name→autocomplete
      key; NOT the drop-3-digits shortcut) cached on securities; CLI
      `python -m desk.maya_ids`. Verified live: 629/813/2093/2547/822, re-run
      resolves 0
- [x] desk/collect_maya.py — one harvest/run → per-company POST feed → filings
      INSERT ON CONFLICT DO NOTHING. Fail-soft: gate-not-cleared or bad JSON
      exits 0, never crashes. Verified live: 100 filings inserted with
      mayafiles doc_urls, re-run inserted 0 (dedup)
- [x] collect.yml: Chromium install + maya step after prices; requirements.txt
      gains playwright

## Phase 2c-1: security onboarding engine (backend) — DONE (2026-07-13)
- [x] desk/onboarding.py — suggest() / resolve() / add_to_db(). Reuses SEC
      ticker map (US), MAYA search + 2-hop companyId (desk/maya_ids), and the
      yfinance NaN guard from collect_prices (junk .TA → manual, never a
      guessed price). Fail-soft everywhere; no-guess policy.
- [x] desk/onboard_cli.py — `python -m desk.onboard_cli {suggest|resolve}`
      (resolve --add to persist).
- [x] Validated live (research/ONBOARDING_VALIDATION.md): US clean
      (AAPL/MSFT/TEVA ADR), TASE clean (822015/1242882/629014 → yfinance),
      Sano 813014 + Bio-Dvash 1082346 → manual (NaN trap), garbage → NotFound,
      add_to_db idempotent (re-run unchanged).
## Phase 2c-1b: company name → primary stock — DONE (2026-07-13)
- [x] Closes the 2c-1 OPEN ISSUE. Investigation found
      `api/v1/companies/<id>/details.mainSecurityId` = MAYA's authoritative
      primary/ordinary share (verified 7 companies incl. Bio-Dvash cross-check
      and a no-stock company → null). See research/COMPANY_PRIMARY_FINDINGS.md.
- [x] desk/onboarding.py: `resolve_company_to_primary_stock(company_id)` (None
      for bond-only/no-stock — never guesses a series); `_maya_suggest` now
      turns company name hits into resolvable primary-stock suggestions, and
      surfaces no-stock companies as NOT-RESOLVABLE-BY-NAME.
- [x] Verified: suggest "בנק לאומי" → 604611; no-stock → clean message;
      Sano 813014 / Bagira 1242882 / AAPL unchanged (no regression).

## Phase 2c-1c: macro news + category tagging — DONE (2026-07-13)
- [x] Schema: `news.category` ('stock' | 'macro', default 'stock', idempotent
      ALTER; existing rows backfill to 'stock') — desk/db.py
- [x] desk/collect_macro.py — Globes RSS macro feeds (iID=2 home/economy,
      iID=585 capital markets; MACRO_FEEDS config) → news category='macro',
      sec_id NULL, url dedup, fail-soft. Verified live: 25 rows inserted,
      re-run 0 new.
- [x] Email macro rule (read-time, no schema change): sec_id NULL = macro,
      NOT NULL = stock. Documented in README.
- [x] Three-way panel filter (My stocks / Macro & reviews / All) proven
      expressible as queries; documented in README.
- [x] collect.yml: macro step after news.

## Phase 2c-2: React UI ("GOLD")
- [x] Step 1 — skeleton + Supabase auth (login only) — DONE (2026-07-13).
      Vite+React (JS) in web/; Supabase signInWithPassword; dark RTL Hebrew
      login matching design_reference; post-login placeholder (email +
      logout). No data yet. `cd web && npm run dev`. web/.env (gitignored)
      holds VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY (public anon key).
- [x] Step 2 — live watchlist table (READ-only) — DONE (2026-07-14).
      web/src/useWatchlist.js reads securities+quotes via the Supabase JS
      client; web/src/Watchlist.jsx renders the desktop RTL table (נייר /
      מחיר / יומי / חודש / רבעון / שנה / 12ח׳), green/red returns, ידני tag +
      "—" daily for manual rows, ₪/$ currency, count + loading/empty/error
      states. Verified visually against representative data.
- [ ] **TODO(auth-mapping):** `watchlist.user_id` references our own `users`
      table, not the Supabase Auth uid. Step 2 reads the seeded "owner"
      user's watchlist as a stand-in. Wire auth-uid ↔ users.id in a later
      step so each user sees their own watchlist.
- [ ] **RLS note:** tables were created by the Python collectors (raw SQL), so
      the anon role may lack SELECT. If the table shows a permission/RLS
      error, grant read access (see CLAUDE.md / the error message).
- [x] Step 3 — unified news/email/filings panel — DONE (2026-07-14).
      Left panel: web/src/useNews.js fetches news+emails+filings once;
      web/src/News.jsx merges FOUR source types (web news, email, MAYA, SEC)
      into one time-sorted feed with source-type badges (outlet/מייל/מאיה/SEC)
      and three tabs — המניות שלי / מאקרו וסקירות / הכל (default הכל). Two-panel
      layout (watchlist right, news left). Verified rendering + filtering +
      tab switching against representative data.
- [ ] **RLS for news/emails/filings:** verified these 3 tables return 0 rows
      with no error via the anon key (RLS-without-policy). Add read policies
      (see CLAUDE.md) so the feed populates; the fetch code is already correct.
- [x] Step 4a — GLOBAL onboarding resolver — DONE (2026-07-14). Yahoo-search
      resolve-assisted global equities (EQUITY-filtered, never auto-pick,
      collision-safe); Hebrew/number → MAYA, Latin → SEC+Yahoo merged. GBp
      (London pence) ÷100 handled in collect_prices alongside ILA agorot
      (normalize_currency). Verified live (research/ONBOARDING_GLOBAL_VALIDATION.md):
      SAP.DE/7203.T/NESN.SW/ASML.AS resolve with correct currency, HSBA.L
      GBp→GBP, collisions surface multiple, US/TASE/Hebrew unchanged.
- [x] Step 4b-1 — searchable tase_securities table — DONE (2026-07-14).
      Browserless MAYA (plain GET, no Playwright, no Origin) enumerates
      companies via autocomplete prefixes + watchlist coverage, resolves each
      company's primary stock via companies/<id>/details, upserts into
      `tase_securities` (security_number PK, Hebrew name, company_id, type,
      is_primary_stock). desk/collect_tase_list.py + daily
      .github/workflows/tase_list.yml. Verified: sample search
      name ILIKE '%טבע%' → Teva; re-run idempotent. See
      research/TASE_LIST_FINDINGS.md.
- [x] Step 4b-1b — widen coverage via companyId sweep — DONE (2026-07-14).
      Replaced prefix-autocomplete (capped 50/prefix) with a full companyId
      sweep (range ~100..2650) — COMPLETE primary-stock coverage: 557 TASE
      companies (up from 439). Resumable (skip company_ids fresh <20h),
      retry on transient errors, progress logging every 100. Store the full
      registered name (`longName`, e.g. "בנק לאומי לישראל בע\"מ") so "בנק"
      search now returns the major banks (was only 1). Daily workflow retained
      (sweep ~10 min).
- [x] Step 4b-2 — Edge Function for Yahoo (global) + SEC (US) live search
      (CORS proxy; browser can't call them directly) — DONE (2026-07-15).
      supabase/functions/search/index.ts (Deno/TS): thin proxy, NOT a port of
      the Python engine. Yahoo search EQUITY-filtered (browser UA) + SEC
      company_tickers.json fetched once and cached in module scope (24h TTL,
      in-flight dedupe — never per keystroke); upstream headers built from
      scratch (caller's Origin never forwarded); CORS allowlist =
      localhost/127.0.0.1 any port + *.vercel.app; JWT verification left ON
      (anon key required — not an open proxy). Israel deliberately excluded
      (tase_securities is queried directly by the UI); Hebrew queries return a
      note. Never auto-picks — always a candidate list.
      Verified locally under Deno against LIVE upstreams: SAP → SAP (US) +
      SAP.DE + SAP.TO (collisions preserved), AAPL/apple/nestle → NESN.SW,
      Hebrew → local-table note, empty q → 400, preflight + disallowed origin
      (ACAO null) correct, and fail-soft proven with a broken SEC URL (HTTP 200,
      Yahoo results + note).
      **Ranking is deliberately NOT a copy of onboarding.py's:** SEC matches are
      scored (exact ticker > query-starts-a-word-in-name > ticker prefix > loose
      substring) and the merge INTERLEAVES US/GLOBAL. Straight concatenation +
      substring matching let junk (CHESAPEAKE contains "sap") fill all 8 slots
      and drop SAP.DE entirely.
      - [ ] **Not yet deployed/verified from the cloud** — needs `supabase login`
            + `link` (browser step). The deploy also answers the open
            datacenter-IP question from EDGE_SEARCH_FINDINGS.md for Yahoo/SEC.
      - [ ] Add the real app origin to ALLOWED_ORIGIN_RE when the UI is
            deployed (Vercel step).
- [x] Step 4b-3 — search + picker + add/remove in the UI — DONE (2026-07-15).
      web/src/useSearch.js routes by query: Hebrew or bare digits →
      `tase_securities` directly (ilike name / prefix-match security_number);
      Latin → the `search` Edge Function via supabase.functions.invoke (sends
      the anon key/JWT). Debounced 300ms with an out-of-order guard. NEVER
      auto-picks. web/src/SearchBox.jsx = input + dropdown picker (market badge
      ת"א/US/GLOBAL, loading/"לא נמצאו תוצאות"/error states, Edge notes[]
      surfaced). useWatchlist gains add()/remove(): optimistic UI, shallow
      insert into `securities` (ON CONFLICT DO NOTHING — never downgrades an
      enriched row) + `watchlist` upsert; remove deletes the watchlist row ONLY
      (security/news/filings are shared and survive). No quotes row renders as
      a gold "ממתין לנתונים" badge. Verified: build + oxlint clean, routeQuery
      unit-tested against the real module (13 cases).
      - [ ] **BLOCKED until the RLS SQL is run** (given to the user, not run by
            us): read policy for `tase_securities` (it never had one — RLS
            without a policy returns 0 rows and NO error, so search looks empty
            but is a permission block), plus INSERT on `securities`,
            INSERT/DELETE on `watchlist`, and USAGE on `watchlist_id_seq`
            (SERIAL pk — insert fails without it). Live search/add/remove is
            unverified until then.
      - [ ] **TASE adds do NOT get prices automatically** — the cron
            (collect.yml) runs news/macro/email/prices/maya but NOT
            `desk.maya_ids` or the onboarding resolver. `tase_securities.symbol`
            is always NULL (no free number→ticker source), so a TASE pick is
            inserted price_source='manual' with yahoo_symbol NULL and stays at
            "ממתין לנתונים" until someone runs
            `python -m desk.onboard_cli resolve TASE <number> --add` (which
            resolves the ticker + maya_company_id and can upgrade
            manual→yfinance). US/GLOBAL adds DO fill in within ~15 min.
            **Fix properly by adding an enrichment step to collect.yml**
            (maya_ids + an onboarding pass over unenriched securities) — the
            collectors were out of scope for this step.
- [x] Step 5a — persist daily price history (backend) — DONE (2026-07-15).
      Schema: `price_history` (sec_id FK + price_date composite PK, close;
      ix_price_history_sec_date on (sec_id, price_date desc)) — new table, so
      create_all(checkfirst=True) adds it with no ALTER. desk/db.py gains
      `upsert_many()` (executemany ON CONFLICT DO UPDATE — the bulk sibling of
      upsert(); ~250 rows/security/day would be ~250 round trips otherwise).
      collect_prices persists the last ~1 year (CHART_DAYS=365) of closes from
      the SAME ~400d frame it already pulls — **no extra yfinance calls** — on
      the daily anchor refresh only (the 12d intra-day runs would rewrite the
      same closes for nothing). Closes are NORMALIZED via the existing `scale`
      from normalize_currency(), so the stored value equals quotes.last_price
      exactly (one ÷100, never repeated). Manual tier mirrors manual_prices
      points as-is (scale 1.0, sparse, nothing interpolated). Retention
      RETENTION_DAYS=400 (~13mo), pruned each run; per-tier row counts logged.
      Verified live on throwaway SQLite: TEVA 223 closes @ 53.74–113.50 **ILS**
      (not agorot), HSBA.L 253 @ 9.18–14.90 **GBP** (not pence), AAPL 252 USD,
      Sano exactly its 2 entered points; latest history close == quotes.last_price
      for all three; prune removed a seeded 500-day-old row; re-run with anchors
      forced stale re-wrote 728 rows with **0 duplicates** and corrected TEVA
      95.15→95.03 (upsert, not insert).
      - [ ] **Not yet run against the live DB** — needs `python -m desk.collect_prices`
            with $env:DESK_DB_URL set (user).
      - [ ] **RLS:** `price_history` will need a read policy before 5b can chart
            it (`create policy "anon read" on public.price_history for select to
            anon, authenticated using (true);`) — same silent-empty trap as the
            other UI-read tables.
- [x] Step 5b — security detail page + chart — DONE (2026-07-15).
      Full-screen page (not a drawer) replacing the dashboard via plain state in
      App (`openSecId`; one page — no router). Clicking a watchlist row opens
      it; the × calls stopPropagation so remove never also navigates; "חזרה"
      returns. web/src/Detail.jsx = numbers (price+currency, daily, MTD/QTD/YTD/
      12M, same green/red/"—" rules) + chart + that security's feed.
      web/src/Chart.jsx = hand-rolled SVG line chart — **no charting dependency**
      (nothing to npm install); time runs left→right so the newest point is on
      the right (SVG coords aren't mirrored by RTL); gold accent line, since a
      chart is decorative and grn/red stay reserved for returns.
      web/src/usePriceHistory.js fetches the full stored series ONCE; the
      חודש/רבעון/שנה selector slices it client-side (never refetches).
      Honest empty states instead of misleading lines: manual tier →
      "מחיר ידני, נכון ל-<date>" + no chart/selector; <5 points → "אין מספיק
      היסטוריה"; a period slice with <2 points → "אין מספיק היסטוריה בתקופה זו".
      Feed reuses the extracted web/src/FeedItem.jsx (shared with News.jsx, so
      badges can't drift) via `useSecurityFeed(secId)` — filtered **server-side**
      by sec_id on all three tables, security tag omitted (redundant inside one
      security).
      Verified: build + oxlint clean; 25 assertions on period slicing, chart
      geometry (oldest→left/newest→right, min→bottom/max→top, flat series
      doesn't divide by zero, TEVA's real ILS range fits the viewBox), the
      point-count thresholds against 5a's real counts (AAPL 252 / TEVA 223 /
      Bagira 23 chart; 1-point and manual don't), and feed scoping.
      - [ ] **Needs the price_history read policy** or the chart shows "אין
            מספיק היסטוריה" for everything (RLS returns an empty array with no
            error). Same trap still open on `news`/`emails`/`filings` — until
            those have policies the detail feed will look empty too.
      - [ ] Not visually verified in a browser (no browser here) — worth a look.
- [x] Step 6b-1 — per-user auth + tight watchlist RLS — DONE (2026-07-15).
      Closes the TODO(auth-mapping) open since step 2 **and** the hole where any
      logged-in user could read/modify any watchlist.
      **Design: (a) `users.auth_uid`** (uuid, nullable, unique) bridging
      auth.uid() -> users.id — chosen over (b) "watchlist.user_id = auth uuid"
      because (a) is purely additive/idempotent, preserves the seeded rows and
      the FK, and keeps `desk/seed.py` + `init_db`'s DESK_DEFAULT_USER seeding
      working (both map by username -> integer id, which (b) would break along
      with a destructive Integer->UUID type change on a live FK'd table).
      Nullable = users rows without a login stay valid (seed/collectors);
      unique = one auth account can never map to two rows (NULLs stay distinct).
      desk/db.py: column + idempotent ALTER (UUID on PG, CHAR(32) on SQLite) +
      `CREATE UNIQUE INDEX IF NOT EXISTS uq_users_auth_uid`.
      web/: `useWatchlist(authUser)` resolves the session user's users.id (and
      self-provisions a row on first login, keyed on auth_uid so tabs/re-logins
      never duplicate); the hardcoded 'owner' is gone. Effect deps are the
      primitive uid/email, not the session object (which Supabase replaces on
      every token refresh).
      **Collectors unaffected** — verified they join watchlist on sec_id and
      never reference user_id, so the union across all users is unchanged; they
      also connect as the table owner, which bypasses RLS.
      Verified on a throwaway SQLite built with the OLD schema + data: migration
      adds auth_uid and preserves the owner + 3 watchlist rows, init_db is
      idempotent (3x, no loss), multiple unlinked users allowed, duplicate
      auth_uid rejected, collector union spans all users and de-dupes overlaps,
      fresh create_all includes the column. Frontend build + oxlint clean.
      - [ ] **RUN `sql/6b-1_per_user_auth_rls.sql`** — section 1 (link 'owner'
            to the auth account) must run BEFORE the next UI login, or first
            login provisions a new empty user and the watchlist looks empty
            (section 6 recovers). Sections 2-3 replace the using(true) policies.
      - [ ] `securities` INSERT stays open to authenticated **by design** —
            adding a security is a shared/global act; what's personal is the
            watchlist row, now locked.
      - [ ] Browser test after the SQL: my rows still there; a 2nd test user
            sees an EMPTY watchlist and none of mine.
- [ ] Step 6b-2 — Vercel deploy. Prep DONE (2026-07-15): web/DEPLOY.md is the
      ordered checklist. **No code/config change was needed** — no vercel.json
      (Root Directory is a project setting a config file can't set, and the Vite
      preset already yields `npm run build` → `dist`; a file would only restate
      defaults and drift). Grepped web/src: nothing hardcodes localhost, and
      `functions.invoke('search')` resolves against VITE_SUPABASE_URL.
      Settings: Root Directory `web` (the only non-default), preset Vite,
      everything else default. `.env`/`dist` are gitignored; only `.env.example`
      is tracked.
      - [ ] **RUN sql/6b-1_per_user_auth_rls.sql FIRST.** The anon key is public
            in the bundle by design; until 6b-1 lands `watchlist` still has
            `anon read using(true)`, so a public URL would expose every
            watchlist to anyone who loads the page, logged in or not.
      - [ ] Push BEFORE connecting Vercel (it builds from GitHub, not the laptop).
      - [ ] Add VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY (Production+Preview)
            **before the first build** — Vite inlines them at BUILD time; adding
            them later needs a Redeploy.
      - [ ] Supabase Site URL / Redirect URLs: **not required for login** (we
            only use signInWithPassword — no magic link/OAuth/confirmation
            redirect), but set them anyway or future password-reset emails will
            point at the localhost:3000 default.
      - [ ] Edge CORS: `*.vercel.app` already covers production AND preview
            (verified against real URL shapes) — **no function redeploy needed**.
            A custom domain would need adding to ALLOWED_ORIGIN_RE + redeploy.
      - [ ] **Tighten ALLOWED_ORIGIN_RE once the URL is known** — `*.vercel.app`
            currently lets ANY vercel.app-hosted site call our function. Low
            impact (public-data search proxy, JWT on) but the bound is weak
            since the anon key is public.
- [ ] Step 6c — UI polish: draggable panel divider, sticky name column, mobile
      layout.

## Open items (carried over)
- [ ] Decide bond price source (DataHub paid EOD vs manual tier) — manual
      tier now exists as a stopgap for unpriced securities
- [ ] Sign up to TASE DataHub, verify free "Securities (Basic)" fields
