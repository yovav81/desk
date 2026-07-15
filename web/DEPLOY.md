# Deploying GOLD (web/) to Vercel

The app is a Vite + React SPA in the `web/` subfolder of the `desk` repo.
Backend is Supabase; there is no server of our own.

## 0. BEFORE YOU DEPLOY — run the RLS SQL (`sql/6b-1_per_user_auth_rls.sql`)

**This is a security gate, not a formality.** The anon key is public *by design*
— it ships inside the browser bundle, and once the site is on the internet
anyone can read it out of the JS. What keeps data safe is RLS, not the key.

Until 6b-1 is applied, `watchlist` still has `anon read using(true)`, which
means **anyone who loads the deployed page could read (and modify) every user's
watchlist without even logging in.** That's tolerable while the app only runs on
localhost. It is not tolerable on a public URL.

So: run the 6b-1 SQL first, confirm your own login still shows your securities,
*then* deploy.

## 1. Push first

Vercel builds from GitHub — it deploys whatever is on the branch, not what's on
your laptop. Push before connecting the project.

## 2. Vercel project settings

**Approach: dashboard settings, no `vercel.json`.** A config file wouldn't save
a single step here — Root Directory is a *project* setting that `vercel.json`
cannot set — and Vercel's Vite preset already produces exactly the build we
want. A config file would only restate the defaults and then drift from them.

New Project → import `yovav81/desk` (it's private; grant Vercel access to the
repo) → set:

| Setting | Value | Note |
|---|---|---|
| Root Directory | `web` | **The only non-default.** The repo root has no package.json. |
| Framework Preset | Vite | auto-detected |
| Build Command | `npm run build` | preset default — leave it |
| Output Directory | `dist` | preset default — leave it |
| Install Command | `npm install` | preset default — leave it |

No SPA rewrite rule is needed: the app has no router (the detail page is client
state), so only `/` is ever requested.

## 3. Environment variables — add them BEFORE the first build

Project Settings → Environment Variables. Add both, ticking **Production** and
**Preview** (and Development if you ever run `vercel dev`):

- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`

Paste the **same values as your local `web/.env`** (Supabase → Project Settings
→ API). The anon key is the public frontend key — safe in the bundle. **Never**
add `DESK_DB_URL` or any service_role key here; those stay backend-only, in the
collectors' GitHub Actions secrets.

**Vite inlines `VITE_*` at BUILD time, not runtime.** If a build runs before
these exist, the bundle ships with `undefined` and the app dies with
"Missing VITE_SUPABASE_URL" in the console — adding the vars later does nothing
until you **Redeploy**.

## 4. Supabase Auth URL configuration

Authentication → URL Configuration:

- **Site URL:** `https://<your-app>.vercel.app`
- **Redirect URLs:** add `https://<your-app>.vercel.app/**` (and
  `https://*-<your-scope>.vercel.app/**` if you want preview deploys covered)

**Login does not actually depend on this.** The app uses only
`signInWithPassword` — no magic link, no OAuth, no email confirmation redirect —
so email/password login works from the Vercel URL with zero URL config. Set it
anyway: Site URL defaults to `localhost:3000`, and the moment you use a flow
that sends a link (password reset, email confirmation for a new employee), that
link would point at localhost and be dead.

## 5. Edge Function CORS

Already covered — `ALLOWED_ORIGIN_RE` in `supabase/functions/search/index.ts`
matches `*.vercel.app`, verified against production (`desk-*.vercel.app`) and
preview (`desk-git-main-*`, `desk-<hash>-*`) URL shapes. **No redeploy needed
for a `.vercel.app` domain.**

Two cases that DO need a code change + `npx supabase@latest functions deploy search`:

1. **A custom domain** (e.g. `gold.example.com`) — not matched; add it to
   `ALLOWED_ORIGIN_RE`.
2. **Tightening the allowlist (recommended once the URL is known):**
   `*.vercel.app` currently allows *any* site hosted on vercel.app to call our
   function, not just ours. Impact is limited — it's a search proxy over public
   Yahoo/SEC data, and JWT verification is on — but the anon key is public, so
   the bound is weak. Once you know the real domain, narrow it, e.g.
   `/^https:\/\/desk(-[a-z0-9-]+)?\.vercel\.app$/i`.

## 6. After the first deploy

- Load the site, log in, confirm your watchlist is yours.
- Search: Hebrew (`בנק`) hits `tase_securities` directly; Latin (`SAP`) goes
  through the Edge Function — if the latter fails with a CORS error, the origin
  isn't matching §5.
- Check the browser console for `Missing VITE_SUPABASE_URL` → §3.
- Optional: Deployment Protection (Project Settings → Deployment Protection) if
  you don't want preview URLs reachable by anyone holding the link. The app
  requires login regardless.
