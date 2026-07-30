-- 009 (Phase 16C): mark when asset_type was last resolved from yfinance.
-- NULL = never checked. Applied in prod 2026-07-28.
alter table public.securities
  add column if not exists asset_type_checked_at timestamptz;