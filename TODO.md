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
- [x] **TODO(auth-mapping)** — closed by 6b-1 (2026-07-15): users.auth_uid
      bridges auth.uid() ↔ users.id; per-user RLS live and verified.
- [x] **RLS note** — read policies applied to every UI-read table (2026-07-16).
- [x] Step 3 — unified news/email/filings panel — DONE (2026-07-14).
      Left panel: web/src/useNews.js fetches news+emails+filings once;
      web/src/News.jsx merges FOUR source types (web news, email, MAYA, SEC)
      into one time-sorted feed with source-type badges (outlet/מייל/מאיה/SEC)
      and three tabs — המניות שלי / מאקרו וסקירות / הכל (default הכל). Two-panel
      layout (watchlist right, news left). Verified rendering + filtering +
      tab switching against representative data.
- [x] **RLS for news/emails/filings** — read policies applied; feed populates
      live (2026-07-16).
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
      - [x] Deployed + verified from the cloud (2026-07-15): q=SAP/AAPL return
            candidates, notes[] empty — the datacenter-IP question for
            Yahoo/SEC is answered (works).
      - [x] `*.vercel.app` covers the deployed app; narrowing to the exact URL
            is an open item (low).
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
      - [x] RLS SQL run; live search/add/remove verified in production
            (2026-07-16, bilingual search verified on the deployed site).
      - [x] **TASE adds now self-enrich** — closed by Phase 6 (2026-07-17):
            desk/collect_enrich.py resolves number→ticker via constructed ISIN
            in collect.yml before prices. (The old note's "run maya_ids +
            onboarding in cron" framing was stale — neither resolves a ticker;
            see research/TASE_ENRICHMENT_FINDINGS.md.)
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
      - [x] Run against the live DB; history populated (2026-07-15/16).
      - [x] `price_history` read policy applied (2026-07-16).
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
      - [x] price_history + news/emails/filings read policies applied; chart
            and detail feed populate live (2026-07-16).
      - [x] Visually verified on the deployed site (2026-07-16).
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
      - [x] `sql/6b-1_per_user_auth_rls.sql` run; 'owner' linked (2026-07-15).
      - [x] `securities` INSERT stays open to authenticated **by design** —
            adding a security is a shared/global act; the watchlist row is the
            personal part, now locked.
      - [x] Browser-tested in production: owner's rows intact; **test2 sees an
            EMPTY watchlist** — per-user RLS verified (2026-07-16).
- [x] Step 6b-2 — Vercel deploy — DONE (2026-07-16). **LIVE at
      desk-henna.vercel.app.** No vercel.json (Root Directory `web` in the
      dashboard, Vite preset defaults); checklist web/DEPLOY.md. 6b-1 RLS ran
      first, env vars set before first build, Supabase Auth URLs configured,
      Vercel auto-deploys on push. Verified live: per-user RLS (test2 empty),
      bilingual search, add/remove, detail page.
      - [ ] Tighten ALLOWED_ORIGIN_RE from `*.vercel.app` to the exact app URL
            (low; see Open items).
- [ ] Step 6c — UI polish: draggable panel divider, sticky name column, mobile
      layout.

## Phase 3: SEC filings collector — DONE (2026-07-15)
- [x] Investigation (research/SEC_COLLECTOR_FINDINGS.md): submissions endpoint,
      1000-filing recent block, UA rule measured (no descriptive UA → 403),
      accession number overflows INTEGER → own dedup column.
- [x] Schema sql/002: filings.accession_no VARCHAR(32) + UNIQUE(source,
      accession_no) (unique INDEX for idempotency), maya_id now nullable,
      securities.cik INTEGER. Rollback script included. db.py caught up.
- [x] desk/sec_ids.py — ticker→CIK backfill CLI (dry-run default, --commit;
      `cik_to_path()` = THE one zero-padding site). All 5 US securities have
      CIKs (AAPL/BAC/C/MSFT/SAP).
- [x] desk/collect_sec.py — submissions JSON per CIK, form allowlist
      (10-K/10-Q/8-K/DEF 14A/20-F/6-K + /A; unfiltered feed is ~59% Form 4
      noise; SAP is a foreign issuer → files 20-F/6-K), Hebrew titles
      ("דוח שנתי (10-K)" etc.), 90-day window, doc URLs to sec.gov Archives.
      Verified in production: 16 filings inserted, re-run 0 (dedup), Hebrew
      titles render in the live UI.
- [x] Wired into CI with SEC_USER_AGENT secret (now in filings.yml).

## Phase 4: timestamps + scheduling — DONE (2026-07-17)
- [x] **SEC timezone bug** — acceptanceDateTime is US Eastern mislabelled 'Z'
      (measured: same instant as the ATOM feed's -04:00). Fixed in code
      (zoneinfo America/New_York), backfilled via sql/003. VERIFIED: BAC 8-K =
      14:45Z = 10:45 NY market open. SEC half of sql/003 is CORRECT and stays.
- [x] **MAYA timezone saga — documented honestly:** publishDate is naive Israel
      local; the old code stored it 3h late. The CODE fix (Asia/Jerusalem) was
      correct, but the sql/003 MAYA backfill was WRONG (double-shifted / hit
      rows it shouldn't). RESOLUTION: deleted all maya rows and re-collected
      from source; now verified correct against the MAYA website. The
      applied_migrations guard remains for the SEC half. Full post-mortem:
      research/FRESHNESS_FINDINGS.md (three models of one field, kept on
      purpose).
- [x] **Scheduling saga:** GitHub `schedule` measured 74–180 min apart despite
      */15 and */5 crons; phase-shifted crons + a dedicated lightweight
      filings.yml (D2) did NOT fix it (documented best-effort/droppable; paying
      doesn't help). Repo made PUBLIC (full-history secret scan:
      research/PUBLIC_REPO_SECRET_SCAN.md, GO, zero credentials ever committed)
      → Actions minutes free.
- [x] **SOLUTION (Path A): Supabase pg_cron + pg_net + Vault** POST
      workflow_dispatch to GitHub. Jobs: desk-dispatch-filings ('*/5') and
      desk-dispatch-collect ('2,17,32,47'). Fine-grained PAT (Actions:write,
      desk repo only, 90-day expiry ~2026-10-14) in Vault as
      'gh_dispatch_token'. MEASURED: dispatch→run-start <1 min (3/3 incl.
      off-hours); end-to-end filing→dashboard ~7 min. The yml `schedule:`
      blocks remain as free lazy fallback (dedup absorbs overlaps).

## Phase 5: frontend freshness + labels — DONE (2026-07-16)
- [x] Feed tags show securities.name (fallback symbol||sec_id) — was the bare
      security number (App.jsx secLabels).
- [x] Auto-refresh: refetch on visibilitychange + 3-min interval, paused while
      the tab is hidden; one timer drives useWatchlist + useNews via
      refreshTick. No more manual F5.
- [x] "הפריט האחרון" header from max(published_at) across the feed — honest
      content freshness ("when the browser fetched" rejected as misleading).
      Deployed via Vercel auto-deploy. Findings:
      research/FRONTEND_FRESHNESS_FINDINGS.md.

## Phase 6: TASE ticker enrichment — DONE (2026-07-17)
- [x] Investigation (research/TASE_ENRICHMENT_FINDINGS.md): the blocker for
      UI-added TASE securities is yahoo_symbol; NO automated number→ticker
      path existed (seeded rows worked only because a human typed tickers into
      data/securities.csv; the old "maya_ids+onboarding in cron" TODO was stale).
- [x] Method discovered + validated: constructed ISIN ("IL"+zfill(9)+Luhn) →
      Yahoo search → **TLV exchange gate** → name logged. Bare number and
      <number>.TA fail 0/6; ISIN 6/6. Scale test n=50 random (seed 20260716):
      **92% match, ZERO wrong-company**; failures structural (foreign
      incorporation — Kenon; Yahoo gaps) and fail SAFE. Camtek proved the TLV
      gate (ISIN returned only its NASDAQ line).
- [x] **Overturned a Phase 0 "fact":** Sano = SANO1.TA and Bio Dvash = BHNY.TA
      exist on Yahoo with real prices — Phase 0 had probed GUESSED tickers
      (SANO.TA/BDVSH.TA). They keep manual prices until a deliberate migration
      (has_manual_prices() blocks the tier flip, loudly).
- [x] desk/collect_enrich.py (dry-run default, --commit in CI; MAX_PER_RUN=25;
      NaN guard before any manual→yfinance flip) wired into collect.yml BEFORE
      prices. Verified live: 8/8 resolved, 6 upgraded, 2 held manual;
      LUMI ₪71.42 / NXSN ₪231.20 / MAXO / DANH auto-priced with full history.

## Open items (priority order)
1. [ ] **Healthchecks.io dead-man monitor** — pg_cron has no retry/alerting; a
       silent stop of the dispatch jobs is invisible until filings go stale.
2. [ ] **PAT rotation ~2026-10-14** — the fine-grained dispatch token expires;
       refresh via Vault `update_secret` one-liner ('gh_dispatch_token').
3. [ ] **Sano/Bio Dvash deliberate migration to yfinance** — after eyeballing
       SANO1.TA/BHNY.TA series vs the hand-entered points (collect_enrich
       stored the symbols, left the tier manual on purpose).
4. [ ] **fetched_at semantics investigation** — overwritten-per-run vs
       ON-CONFLICT-preserved is UNKNOWN (evidence destroyed by the maya
       re-collect); nothing may be built on it until resolved (lesson 3d).
5. [ ] **collector_runs table** — true pipeline freshness ("collectors last
       ran"), deferred from Phase 5; today's header shows newest-item time only.
6. [ ] Node.js 20 deprecation warnings in workflow actions (cosmetic, low).
7. [ ] DESK news dedup upgrade: URL-unique → title-similarity (SECTORS
       finding, deferred).
8. [ ] CORS narrowing: ALLOWED_ORIGIN_RE `*.vercel.app` → the exact app URL
       (low; public-data proxy, JWT on).
- Carried over:
  - [ ] Decide bond price source (DataHub paid EOD vs manual tier) — manual
        tier now exists as a stopgap for unpriced securities
  - [ ] TASE DataHub signup — no longer needed for equity tickers (ISIN
        enrichment covers them); still the authoritative fallback and the only
        candidate source for bonds
