-- ===========================================================================
-- DESK / GOLD — 002: SEC filings collector schema
-- Date: 2026-07-15
--
-- PURPOSE: let `filings` hold SEC EDGAR filings alongside the existing MAYA
-- ones, and cache each US security's CIK. Schema only — no collector exists
-- yet (Phase 3 step 2; the collector is a later step).
--
-- Decisions locked in research/SEC_COLLECTOR_FINDINGS.md:
--   D1 — filings gets accession_no VARCHAR(32) NULL + UNIQUE(source, accession_no);
--        maya_id drops NOT NULL.
--   D5 — securities gets cik INTEGER NULL.
--   D7 — the dedup key for SEC filings is (source, accession_no).
--
-- WHY a new column instead of reusing maya_id: an SEC accession number
-- ('0001140361-26-025622') does not fit that column. Digits-only it is
-- 114036126025622, which overflows PostgreSQL INTEGER (max 2147483647) by
-- ~53,000x, and casting to a number also destroys the leading zeros.
--
-- Run in the Supabase SQL editor (service role). IDEMPOTENT — safe to re-run.
-- Rollback: sql/002_sec_collector_rollback.sql
-- ===========================================================================


-- --- 1. filings.accession_no — the SEC dedup key ---------------------------
-- NULL for every MAYA row, forever. 20 chars today; 32 leaves headroom.
alter table public.filings
  add column if not exists accession_no varchar(32);

comment on column public.filings.accession_no is
  'SEC EDGAR accession number (e.g. 0001140361-26-025622). NULL for maya rows. Dedup key for source=''sec''.';


-- --- 2. filings.maya_id — drop NOT NULL ------------------------------------
-- An SEC row has no MAYA id. This is the ONLY change to maya_id: its meaning,
-- type and dedup role are untouched, and uq_filings_source_maya_id is left
-- exactly as it is. Re-running this is a no-op (dropping an absent NOT NULL
-- does not error).
alter table public.filings
  alter column maya_id drop not null;

comment on column public.filings.maya_id is
  'MAYA announcement id. NULL for sec rows. Dedup key for source=''maya'' — sacred, like news.url.';


-- --- 3. UNIQUE (source, accession_no) — the SEC dedup guard ----------------
-- A UNIQUE *INDEX* rather than a table CONSTRAINT, deliberately: Postgres has
-- no ADD CONSTRAINT ... IF NOT EXISTS, so a constraint could not be re-run
-- safely. A unique index enforces uniqueness identically and ON CONFLICT
-- (source, accession_no) DO NOTHING infers from it just the same — so
-- db.insert_ignore() works unchanged.
--
-- *** EFFECT ON EXISTING MAYA ROWS: NONE. ***
-- Every MAYA row will have accession_no = NULL, and PostgreSQL treats NULLs as
-- DISTINCT in a unique index by default — (‘maya’, NULL) never collides with
-- another (‘maya’, NULL). Any number of MAYA rows coexist. (PG 15 added
-- NULLS NOT DISTINCT as an opt-in; it is deliberately NOT used here.)
-- The two guards therefore live side by side, each covering its own tier:
--     maya rows -> uq_filings_source_maya_id (source, maya_id)   [accession_no NULL]
--     sec  rows -> uq_filings_source_accession (source, accession_no) [maya_id NULL]
create unique index if not exists uq_filings_source_accession
  on public.filings (source, accession_no);


-- --- 4. securities.cik -----------------------------------------------------
-- INTEGER is safe: the largest CIK in SEC's company_tickers.json is 2,142,762
-- vs INTEGER's max of 2,147,483,647 — about 1,000x headroom, and CIKs are
-- assigned sequentially. Matches the existing maya_company_id precedent
-- (also INTEGER, also resolved once and cached).
alter table public.securities
  add column if not exists cik integer;

comment on column public.securities.cik is
  'SEC EDGAR CIK for market=''US'' securities; NULL until resolved. Zero-pad to 10 digits for data.sec.gov URLs.';


-- --- 5. VERIFY (read-only) -------------------------------------------------
-- New columns present:
select table_name, column_name, data_type, is_nullable
from information_schema.columns
where table_schema = 'public'
  and (table_name, column_name) in (('filings','accession_no'), ('filings','maya_id'), ('securities','cik'))
order by table_name, column_name;
-- expect: filings.accession_no character varying YES
--         filings.maya_id      integer           YES   <-- NOT NULL now dropped
--         securities.cik       integer           YES

-- Both dedup guards present and independent:
select indexname, indexdef
from pg_indexes
where schemaname = 'public' and tablename = 'filings'
order by indexname;
-- expect uq_filings_source_maya_id AND uq_filings_source_accession, plus
-- ix_filings_published_at and the pkey.

-- Existing MAYA rows untouched (count must equal what it was before this ran):
select source, count(*) as rows, count(maya_id) as with_maya_id, count(accession_no) as with_accession
from public.filings
group by source;
-- expect: maya | N | N | 0
