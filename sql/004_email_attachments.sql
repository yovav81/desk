-- ===========================================================================
-- DESK / GOLD — 004: email attachments (metadata table + access policies)
-- Date: 2026-07-17
--
-- PURPOSE: store attachment METADATA for emails; the FILES live in a PRIVATE
-- Supabase Storage bucket and are reachable only via signed URLs. Metadata is
-- as shared as the emails feed itself (anon read, like `emails`) — the
-- protection is the private bucket, not hidden metadata.
--
-- ⚠️ MANUAL STEP FIRST — CREATE THE BUCKET IN THE DASHBOARD (SQL can't click):
--   Storage → New bucket →
--     name:                email-attachments      (exactly; the collector hardcodes it)
--     Public bucket:       OFF  (PRIVATE — analyst reports must never be world-readable)
--     File size limit:     20 MB (optional belt; the collector caps at 20 MB anyway)
--     Allowed MIME types:  leave empty (collector filters to pdf/office)
--     Image transformations: OFF / none
--
-- IDEMPOTENT — safe to re-run (create if not exists / drop-and-recreate policies).
-- Rollback: sql/004_email_attachments_rollback.sql
-- ===========================================================================


-- --- 1. metadata table ------------------------------------------------------
-- storage_path is NULLABLE on purpose: an oversized attachment (>20 MB) keeps
-- a metadata row (filename+size visible in the UI) with no stored file.
-- fetched_at = UPLOAD time, not email receive time — retention gives every
-- object 14 days of availability from when it was stored (so backfilled old
-- emails still get their window).
create table if not exists public.email_attachments (
    id serial primary key,
    email_id integer not null references public.emails(id) on delete cascade,
    filename varchar(255) not null,
    size_bytes bigint not null,
    content_type varchar(128),
    storage_path text,
    fetched_at timestamptz not null default now(),
    -- idempotency key: re-running the collector/backfill can never duplicate
    constraint uq_email_attachments_email_file unique (email_id, filename)
);

create index if not exists ix_email_attachments_email_id
  on public.email_attachments (email_id);


-- --- 2. metadata read policy (same exposure as `emails`) --------------------
alter table public.email_attachments enable row level security;
grant select on public.email_attachments to anon, authenticated;

drop policy if exists "anon read" on public.email_attachments;
create policy "anon read" on public.email_attachments
  for select to anon, authenticated using (true);


-- --- 3. Storage policy: signed-URL creation for logged-in users -------------
-- The UI (part 2) mints signed URLs client-side; that requires SELECT on
-- storage.objects for this bucket, for `authenticated` ONLY (anon = logged-out
-- gets nothing; the shared-pool model applies to logged-in employees).
-- If this errors on ownership in the SQL editor, create the same policy via
-- Dashboard → Storage → Policies instead.
drop policy if exists "authenticated read email-attachments" on storage.objects;
create policy "authenticated read email-attachments" on storage.objects
  for select to authenticated
  using (bucket_id = 'email-attachments');


-- --- 4. VERIFY (read-only) ---------------------------------------------------
select column_name, data_type, is_nullable
from information_schema.columns
where table_schema = 'public' and table_name = 'email_attachments'
order by ordinal_position;
-- expect: id / email_id / filename / size_bytes / content_type /
--         storage_path (YES nullable) / fetched_at

select policyname, roles from pg_policies
where tablename in ('email_attachments') or policyname like '%email-attachments%';
-- expect the two policies above.
