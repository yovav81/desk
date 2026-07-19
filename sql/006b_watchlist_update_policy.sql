grant update on public.watchlist to authenticated;

drop policy if exists "own watchlist update" on public.watchlist;
create policy "own watchlist update" on public.watchlist
  for update to authenticated
  using (user_id = (select id from public.users where auth_uid = auth.uid()))
  with check (user_id = (select id from public.users where auth_uid = auth.uid()));