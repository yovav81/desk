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

## Phase 2: (placeholders)
- [ ] Pricing: yfinance quote fetch + MTD/YTD math, incl. .TA ILA→ILS ÷100
- [ ] Decide bond price source (DataHub paid EOD vs manual) — blocks bond support
- [ ] Sign up to TASE DataHub, verify free "Securities (Basic)" fields
- [ ] Hosted, multi-user Streamlit dashboard (mobile-friendly) reading from
      DESK_DB_URL — per-user watchlist view over shared news/email/price data
- [ ] (Later, out of scope) read-only link to existing filings system
