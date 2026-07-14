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
- [ ] Step 4b — search + picker + add/remove security in the UI, over the
      onboarding engine (suggest/resolve/add_to_db).
- [ ] Security detail page.
- [ ] Draggable panel divider + mobile tabs (polish).
- [ ] Deploy (Vercel) — not yet.

## Open items (carried over)
- [ ] Decide bond price source (DataHub paid EOD vs manual tier) — manual
      tier now exists as a stopgap for unpriced securities
- [ ] Sign up to TASE DataHub, verify free "Securities (Basic)" fields
