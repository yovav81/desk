-- 011 (Phase 21): mark mail whose SUBJECT is a Bloomberg per-stock alert.
-- Such a subject is about ONE security by definition; whether we resolved WHICH
-- one (unmapped exchange code -> sec_id stays NULL, by 16D-FIX3) says nothing
-- about whether it is macro. It is not. The dashboard's macro tab excludes it.
-- Measured: 417 sec_id-NULL rows in 9 unmapped codes were cluttering macro.
alter table public.emails
  add column if not exists is_single_stock boolean not null default false;
