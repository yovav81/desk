-- Rollback 010. Drops only the marker column; no email row is touched.
alter table public.emails
  drop column if exists sweep_checked_at;
