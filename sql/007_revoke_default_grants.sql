-- Applied in prod 2026-07-20, Phase 15B

revoke all on all tables in schema public from anon;
revoke insert, update, delete, truncate, references, trigger
  on all tables in schema public from authenticated;

grant insert, update, delete on public.watchlist to authenticated;
grant insert on public.securities to authenticated;
grant insert on public.users to authenticated;
grant update on public.profiles to authenticated;
alter default privileges in schema public revoke all on tables from anon;
alter default privileges in schema public revoke all on tables from authenticated;
