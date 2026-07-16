-- ===========================================================================
-- DESK / GOLD — 003: correct the stored timezone of existing filings
-- Date: 2026-07-15
--
-- PURPOSE: backfill the ~120 maya and 16 sec rows written BEFORE the parser fix
-- in desk/collect_maya.py and desk/collect_sec.py. Both stored a local wall
-- clock mislabelled as UTC, in opposite directions:
--
--   sec  — SEC's submissions JSON labels acceptanceDateTime 'Z' but the value is
--          US/Eastern wall clock. Stored 4h EARLY (5h in winter).
--          Proof: a BAC 8-K stored as 2026-07-14 10:45:08+00 = 06:45 New York,
--          before the market opened.
--   maya — MAYA sends a NAIVE Israel-local timestamp; we stamped UTC on it.
--          Stored 3h LATE (2h in winter).
--          Proof: a filing stored as 2026-07-02 23:25:00+00 = 02:25 Israel local.
--
-- See research/FRESHNESS_FINDINGS.md. Rows written AFTER the parser fix are
-- already correct and MUST NOT be shifted — hence the guard below.
--
-- *** RUN THIS ONLY ONCE THE FIXED COLLECTORS ARE DEPLOYED. ***
-- If a collector runs with the OLD code after this script, it writes new bad
-- rows that this script has already been marked as applied for, and they will
-- NOT be corrected by re-running it.
--
-- IDEMPOTENT: yes, genuinely — see the guard.
-- Rollback: sql/003_fix_filing_timestamps_rollback.sql
-- ===========================================================================


-- --- 0. BEFORE — eyeball the damage ----------------------------------------
-- sec rows should look several hours EARLY (a 06:45 NY 8-K is implausible);
-- maya rows should look several hours LATE (a 02:25 Israel filing is implausible).
select source,
       count(*) as rows,
       min(published_at) as oldest,
       max(published_at) as newest
from public.filings
where published_at is not null
group by source
order by source;

select id, source, published_at,
       published_at at time zone 'America/New_York' as looks_like_ny,
       published_at at time zone 'Asia/Jerusalem'   as looks_like_israel,
       left(title, 40) as title
from public.filings
where published_at is not null
order by published_at desc
limit 15;


-- --- 1. THE GUARD ----------------------------------------------------------
-- HOW DOUBLE-SHIFTING IS PREVENTED, precisely:
-- A timestamp shift is not self-identifying — a corrected row looks exactly
-- like an uncorrected one, so no WHERE clause on `filings` alone can tell them
-- apart, and a second run WOULD shift again (8h/6h of damage). The only honest
-- way to make this re-runnable is to record that it ran. This table is that
-- record: the UPDATEs live inside a block that refuses to act if the marker is
-- already present, and the marker is written in the SAME TRANSACTION as the
-- UPDATEs — so they either both happen or neither does. Re-running is then a
-- no-op that raises a NOTICE.
--
-- NOTE (accepted drift): this table is deliberately NOT declared in desk/db.py.
-- It is an ops ledger, never read by any collector or by the UI.
create table if not exists public.applied_migrations (
    name text primary key,
    applied_at timestamptz not null default now()
);


-- --- 2. THE FIX ------------------------------------------------------------
-- Both conversions use the same two-step AT TIME ZONE idiom, which is
-- DST-CORRECT PER ROW — Postgres picks EDT vs EST (and IDT vs IST) from each
-- row's own date, so winter filings get -05:00/+02:00 automatically. No fixed
-- offset appears anywhere in this script, on purpose.
--
--   published_at AT TIME ZONE 'UTC'   -> the naive wall clock we wrongly stored
--   ... AT TIME ZONE '<real zone>'    -> re-read that wall clock in its TRUE
--                                        zone, yielding a correct instant
--
-- Worked example (BAC 8-K):
--   2026-07-14 10:45:08+00
--     AT TIME ZONE 'UTC'              -> 2026-07-14 10:45:08   (naive)
--     AT TIME ZONE 'America/New_York' -> 2026-07-14 14:45:08+00 (EDT, -4)  ✅
do $$
declare
    n_sec int;
    n_maya int;
begin
    if exists (select 1 from public.applied_migrations
               where name = '003_fix_filing_timestamps') then
        raise notice '003_fix_filing_timestamps already applied — skipping (no double-shift).';
        return;
    end if;

    -- SEC: stored value is New York wall clock -> shift FORWARD to true UTC.
    update public.filings
    set published_at = (published_at at time zone 'UTC') at time zone 'America/New_York'
    where source = 'sec' and published_at is not null;
    get diagnostics n_sec = row_count;

    -- MAYA: stored value is Israel wall clock -> shift BACKWARD to true UTC.
    update public.filings
    set published_at = (published_at at time zone 'UTC') at time zone 'Asia/Jerusalem'
    where source = 'maya' and published_at is not null;
    get diagnostics n_maya = row_count;

    insert into public.applied_migrations (name) values ('003_fix_filing_timestamps');

    raise notice 'shifted sec=% rows forward (Eastern->UTC), maya=% rows back (Israel->UTC)',
                 n_sec, n_maya;
end $$;


-- --- 3. AFTER — verify -----------------------------------------------------
-- sec rows should now sit inside US market/business hours in New York;
-- maya rows inside Israeli business hours in Tel Aviv.
select id, source, published_at,
       published_at at time zone 'America/New_York' as now_in_ny,
       published_at at time zone 'Asia/Jerusalem'   as now_in_israel,
       left(title, 40) as title
from public.filings
where published_at is not null
order by published_at desc
limit 15;

-- The BAC 8-K specifically: 2026-07-14 10:45:08+00 -> 2026-07-14 14:45:08+00
-- (= 10:45 New York, market open). Adjust the title filter if needed.
select id, sec_id, published_at,
       published_at at time zone 'America/New_York' as in_ny
from public.filings
where source = 'sec' and sec_id = 'BAC'
order by published_at desc
limit 5;

-- Guard is recorded:
select * from public.applied_migrations;
