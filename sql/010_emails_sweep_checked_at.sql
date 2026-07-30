-- 010 (Phase 19-FIX): mark when the NULL-sweep last EXAMINED an email.
-- NULL = never swept. Without it the sweep re-reads the newest 200 unattributed
-- rows every run and never reaches older mail (measured: 1,273 NULL rows, only
-- the top 200 ever scanned).
-- To force a full re-sweep after a NEW attribution tier ships:
--   update public.emails set sweep_checked_at = null where sec_id is null;
alter table public.emails
  add column if not exists sweep_checked_at timestamptz;
