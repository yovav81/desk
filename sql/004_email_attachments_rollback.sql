-- ===========================================================================
-- DESK / GOLD — 004 ROLLBACK: remove email attachment metadata + policies
-- Date: 2026-07-17
--
-- Reverses sql/004_email_attachments.sql in reverse order.
--
-- ⚠️ NOTE: this does NOT delete the files in the 'email-attachments' Storage
-- bucket (SQL can't). To fully roll back: empty + delete the bucket in
-- Dashboard → Storage. Dropping the table below loses the mapping from emails
-- to object paths, so do the bucket cleanup FIRST if you want it.
-- The `emails` table (subjects/bodies/attribution) is untouched throughout.
-- ===========================================================================

drop policy if exists "authenticated read email-attachments" on storage.objects;

drop policy if exists "anon read" on public.email_attachments;

drop table if exists public.email_attachments;

-- VERIFY: both should return zero rows.
select * from information_schema.tables
where table_schema = 'public' and table_name = 'email_attachments';
select policyname from pg_policies where policyname like '%email-attachments%';
