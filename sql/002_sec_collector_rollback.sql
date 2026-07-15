-- ===========================================================================
-- DESK / GOLD — 002 ROLLBACK: undo the SEC filings collector schema
-- Date: 2026-07-15
--
-- Reverses every change in sql/002_sec_collector.sql, in reverse order.
--
-- *** READ THIS BEFORE RUNNING — THIS ROLLBACK IS DESTRUCTIVE. ***
-- Step 3 DELETES every SEC filing row. It is not optional: restoring
-- maya_id's NOT NULL constraint FAILS while any row has maya_id NULL, and
-- every sec row has maya_id NULL by design. There is no lossless way back
-- once SEC filings have been collected — the accession numbers go with them.
-- If you only want to stop collecting SEC filings, do NOT run this: just
-- remove the collector step from the workflow and leave the schema alone
-- (the nullable column and the extra index cost nothing).
--
-- MAYA rows are untouched by every statement here.
-- ===========================================================================


-- --- 1. undo §4: securities.cik --------------------------------------------
alter table public.securities
  drop column if exists cik;


-- --- 2. undo §3: the SEC dedup guard ---------------------------------------
-- Dropped BEFORE the delete: the index is worthless without the column, and
-- dropping it first makes the delete cheaper.
drop index if exists public.uq_filings_source_accession;


-- --- 3. DESTRUCTIVE: remove SEC rows so maya_id can be NOT NULL again ------
-- Check what you are about to lose first:
--   select count(*) from public.filings where source = 'sec';
delete from public.filings
where source = 'sec';


-- --- 4. undo §2: restore maya_id NOT NULL ----------------------------------
-- Fails if step 3 was skipped or if any other row has a NULL maya_id.
alter table public.filings
  alter column maya_id set not null;


-- --- 5. undo §1: drop the column -------------------------------------------
alter table public.filings
  drop column if exists accession_no;


-- --- 6. VERIFY (read-only) -------------------------------------------------
select table_name, column_name, is_nullable
from information_schema.columns
where table_schema = 'public'
  and (table_name, column_name) in (('filings','accession_no'), ('filings','maya_id'), ('securities','cik'))
order by table_name, column_name;
-- expect: ONLY filings.maya_id, is_nullable = NO. The other two are gone.

select indexname from pg_indexes
where schemaname = 'public' and tablename = 'filings'
order by indexname;
-- expect uq_filings_source_maya_id still present; uq_filings_source_accession gone.

select source, count(*) from public.filings group by source;
-- expect maya rows only, at their original count.
