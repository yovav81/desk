-- Rollback 011. Drops the flag only — no email row is ever deleted.
alter table public.emails
  drop column if exists is_single_stock;
