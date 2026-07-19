-- ===========================================================================
-- DESK / GOLD — 006 (Phase 13B): persistent manual watchlist ordering.
-- Run manually in the Supabase SQL Editor. Idempotent — safe to re-run.
-- Rollback: sql/006_watchlist_position_rollback.sql
-- ===========================================================================

-- Guard 1: the column is added at most once.
alter table public.watchlist add column if not exists position integer;

-- Guard 2: initialization touches ONLY rows still NULL, appending them after
-- the user's current max — a re-run with nothing NULL is a no-op, and a
-- partially-initialized state is completed, never re-shuffled. Insertion
-- order = watchlist.id (SERIAL, the existing insertion key).
with base as (
  select user_id, coalesce(max(position), 0) as maxpos
  from public.watchlist
  group by user_id
),
ranked as (
  select w.id, b.maxpos + row_number() over (partition by w.user_id order by w.id) as pos
  from public.watchlist w
  join base b using (user_id)
  where w.position is null
)
update public.watchlist w
set position = r.pos
from ranked r
where w.id = r.id;

-- VERIFY: expect 0.
select count(*) as null_positions from public.watchlist where position is null;

-- ---------------------------------------------------------------------------
-- RLS GAP — REPORTED, deliberately NOT applied here (policies are a separate,
-- reviewed decision): sql/6b-1 grants watchlist SELECT/INSERT/DELETE only and
-- has no UPDATE policy. The UI persists reorders via upsert (ON CONFLICT DO
-- UPDATE), which needs an UPDATE grant + an "own watchlist update" policy with
-- the same auth.uid() -> users.id ownership check as the other three. Until
-- that is added, reorder persistence fails and the UI reverts with a toast.
