-- Rollback of 006 (Phase 13B): drop the manual-order column. Watchlist rows
-- themselves are untouched; the UI falls back to name order (position-less).
alter table public.watchlist drop column if exists position;
