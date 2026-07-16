-- ===========================================================================
-- DESK / GOLD — 003 ROLLBACK: restore the ORIGINAL (wrong) filing timestamps
-- Date: 2026-07-15
--
-- Reverses sql/003_fix_filing_timestamps.sql exactly, then clears the marker so
-- the forward script can be run again.
--
-- Reversal is EXACT: the forward fix re-labels a wall clock, it does not lose
-- information, so mapping the instant back through the same zone reproduces the
-- original value to the second.
--
--   forward:  (published_at at time zone 'UTC')       at time zone '<zone>'
--   reverse:  (published_at at time zone '<zone>')    at time zone 'UTC'
--
-- Worked example (BAC 8-K):
--   2026-07-14 14:45:08+00
--     AT TIME ZONE 'America/New_York' -> 2026-07-14 10:45:08   (naive)
--     AT TIME ZONE 'UTC'              -> 2026-07-14 10:45:08+00  = the original
--
-- *** THIS RE-BREAKS THE DATA ON PURPOSE. *** Only run it if the forward script
-- was applied in error, and only while the OLD collector code is deployed —
-- otherwise fixed collectors will keep writing correct rows alongside these
-- re-broken ones, leaving the table in a MIXED state that no single shift can
-- untangle. There is no marker distinguishing the two.
--
-- Guarded the same way as the forward script: it refuses to act unless the
-- forward marker is present, so this cannot shift already-correct data.
-- ===========================================================================


-- --- 1. BEFORE -------------------------------------------------------------
select id, source, published_at, left(title, 40) as title
from public.filings
where published_at is not null
order by published_at desc
limit 15;


-- --- 2. THE REVERSAL -------------------------------------------------------
do $$
declare
    n_sec int;
    n_maya int;
begin
    if not exists (select 1 from public.applied_migrations
                   where name = '003_fix_filing_timestamps') then
        raise notice '003_fix_filing_timestamps is not applied — nothing to roll back.';
        return;
    end if;

    -- SEC: true UTC -> back to New York wall clock mislabelled as UTC.
    update public.filings
    set published_at = (published_at at time zone 'America/New_York') at time zone 'UTC'
    where source = 'sec' and published_at is not null;
    get diagnostics n_sec = row_count;

    -- MAYA: true UTC -> back to Israel wall clock mislabelled as UTC.
    update public.filings
    set published_at = (published_at at time zone 'Asia/Jerusalem') at time zone 'UTC'
    where source = 'maya' and published_at is not null;
    get diagnostics n_maya = row_count;

    delete from public.applied_migrations where name = '003_fix_filing_timestamps';

    raise notice 'REVERTED sec=% rows, maya=% rows; marker cleared', n_sec, n_maya;
end $$;


-- --- 3. AFTER --------------------------------------------------------------
select id, source, published_at,
       published_at at time zone 'America/New_York' as in_ny,
       published_at at time zone 'Asia/Jerusalem'   as in_israel,
       left(title, 40) as title
from public.filings
where published_at is not null
order by published_at desc
limit 15;

-- Marker must be gone (the forward script can run again):
select * from public.applied_migrations;
