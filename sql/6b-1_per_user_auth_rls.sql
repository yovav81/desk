-- ===========================================================================
-- DESK / GOLD — phase 2c-6b-1: per-user auth + tight watchlist RLS
--
-- Run in the Supabase SQL editor (service role — bypasses RLS).
--
-- ORDER MATTERS:
--   1. Deploy the code and let `users.auth_uid` exist (any collector run, or
--      `python -m desk.db`, applies the idempotent migration).
--   2. Run section 1 BELOW **before** logging into the UI again. On first login
--      the app provisions a users row for an unlinked auth account — if 'owner'
--      isn't linked yet, you'd get a NEW empty user and your watchlist would
--      look empty. Section 6 undoes that if it happens.
--
-- Model: SHARED POOL, PERSONAL WATCHLIST.
--   personal -> watchlist (locked to the owning auth user, below)
--   shared   -> securities, quotes, price_history, news, emails, filings,
--               tase_securities (readable by every authenticated user)
-- ===========================================================================


-- --- 0. SANITY -------------------------------------------------------------
-- Expect exactly one row. If empty, run `python -m desk.db` first.
select column_name
from information_schema.columns
where table_schema = 'public' and table_name = 'users' and column_name = 'auth_uid';


-- --- 1. LINK your auth account to the seeded 'owner' row --------------------
-- Your existing securities hang off users.username='owner'. This is the single
-- statement that keeps them yours. It reads the uid straight from auth.users,
-- so there's nothing to copy by hand.
-- (To see it in the dashboard instead: Authentication -> Users -> your row's UID.)
update public.users u
set auth_uid = a.id
from auth.users a
where u.username = 'owner'
  and a.email = 'yovav81@gmail.com';   -- <-- your login email

-- VERIFY: 'owner' must show a non-NULL auth_uid and your real securities count.
select u.id, u.username, u.auth_uid, count(w.sec_id) as securities
from public.users u
left join public.watchlist w on w.user_id = u.id
group by u.id, u.username, u.auth_uid
order by u.id;


-- --- 2. users: own row only, and not readable by anon -----------------------
-- Was: "anon read" using(true) — that exposed every username to anyone holding
-- the public anon key.
drop policy if exists "anon read" on public.users;
revoke select on public.users from anon;

grant select, insert on public.users to authenticated;
grant usage on sequence public.users_id_seq to authenticated;  -- SERIAL pk

create policy "own user select" on public.users
  for select to authenticated
  using (auth_uid = auth.uid());

-- Self-provisioning on first login. WITH CHECK pins the row to the caller's own
-- uid, so nobody can create a row pointing at someone else's auth account.
create policy "own user insert" on public.users
  for insert to authenticated
  with check (auth_uid = auth.uid());


-- --- 3. watchlist: STRICTLY the caller's own rows ---------------------------
-- Was: using(true) for select/insert/delete — ANY logged-in user could read or
-- modify ANY watchlist. This is the hole 6b-1 closes.
drop policy if exists "anon read" on public.watchlist;
drop policy if exists "authenticated insert" on public.watchlist;
drop policy if exists "authenticated delete" on public.watchlist;
revoke select, insert, delete on public.watchlist from anon;

grant select, insert, delete on public.watchlist to authenticated;
grant usage on sequence public.watchlist_id_seq to authenticated;

-- The auth.uid() -> users.id hop. The inner select is itself subject to the
-- users policy above, so it can only ever resolve to the caller's own id.
create policy "own watchlist select" on public.watchlist
  for select to authenticated
  using (user_id in (select id from public.users where auth_uid = auth.uid()));

create policy "own watchlist insert" on public.watchlist
  for insert to authenticated
  with check (user_id in (select id from public.users where auth_uid = auth.uid()));

create policy "own watchlist delete" on public.watchlist
  for delete to authenticated
  using (user_id in (select id from public.users where auth_uid = auth.uid()));


-- --- 4. securities INSERT stays OPEN to authenticated -----------------------
-- DELIBERATE, not an oversight: `securities` is the SHARED pool. Adding a
-- security is a global act (the collectors then gather news/prices/filings for
-- it once, for everyone). What's personal is the *watchlist row* pointing at
-- it, and that is now locked down in section 3. Nothing here needs changing —
-- listed only to make the decision explicit.
--   existing: grant insert on public.securities to authenticated;
--             create policy "authenticated insert" ... with check (true);


-- --- 5. shared read tables — unchanged --------------------------------------
-- securities, quotes, price_history, news, emails, filings, tase_securities
-- keep their read policies. Still outstanding from earlier steps (the feed and
-- chart stay empty without them — RLS with no policy returns 0 rows and NO
-- error):
create policy "anon read" on public.news
  for select to anon, authenticated using (true);
create policy "anon read" on public.emails
  for select to anon, authenticated using (true);
create policy "anon read" on public.filings
  for select to anon, authenticated using (true);
create policy "anon read" on public.price_history
  for select to anon, authenticated using (true);


-- --- 6. RECOVERY (only if you logged in before running section 1) -----------
-- Symptom: your watchlist is empty and a users row exists named after your
-- email. Fix = delete that stray row (guarded: only if it owns no watchlist
-- rows), then re-run section 1.
-- delete from public.users
-- where auth_uid = (select id from auth.users where email = 'yovav81@gmail.com')
--   and username <> 'owner'
--   and id not in (select user_id from public.watchlist);
