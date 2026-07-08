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

## Phase 1: (placeholders)
- [ ] Decide bond price source (DataHub paid EOD vs manual) — blocks bond support
- [ ] Sign up to TASE DataHub, verify free "Securities (Basic)" fields
- [ ] Seed security-number↔symbol mapping (manual TASE export)
- [ ] Watchlist storage format + quote collector (yfinance, ILA→ILS normalization)
- [ ] News collector (Globes RSS, Google News per-company, yfinance news) + dedup
- [ ] Dedicated Gmail inbox + IMAP collector + company tagging
- [ ] Dashboard UI skeleton
- [ ] (Later, out of scope) read-only link to existing filings system
