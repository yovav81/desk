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

## Phase 2b: MAYA filings collector (next)
- [ ] Scope + build MAYA filings collector

## Phase 2c: React UI
- [ ] Hosted, multi-user, mobile-friendly React dashboard, READ-only against
      DESK_DB_URL — per-user watchlist view over shared news/email/price data

## Open items (carried over)
- [ ] Decide bond price source (DataHub paid EOD vs manual tier) — manual
      tier now exists as a stopgap for unpriced securities
- [ ] Sign up to TASE DataHub, verify free "Securities (Basic)" fields
