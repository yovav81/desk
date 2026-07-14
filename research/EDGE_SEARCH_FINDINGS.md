# Edge Function feasibility for live securities search — Phase 2c-4b pre-check

**Date:** 2026-07-14 · **Question:** how do we power instant securities search
(US, global, AND Israel) from the React UI? Specifically — can a Supabase Edge
Function (Deno/TS, **no** headless browser) do all three, and can **MAYA
(Israel) search work WITHOUT Playwright**? Investigation only; throwaway probes,
nothing deployed. Free/public endpoints only.

## TL;DR

| Question | Answer |
|---|---|
| Edge Function: outbound HTTPS `fetch()` to Yahoo/SEC/MAYA? | **Yes.** Deno runtime, arbitrary HTTPS fetch. |
| Edge Function: run Playwright/Chromium? | **No.** No browser, no subprocess, no filesystem for browser binaries. |
| US + global search as plain GETs (browserless)? | **Yes** — Yahoo + SEC work with the right `User-Agent`. |
| **Israeli (MAYA) search WITHOUT Playwright?** | **Yes — plain GET returns real JSON, no harvested cookie.** The Imperva gate does **not** challenge the API GETs. (One caveat: verified from a residential IP; datacenter-IP behavior unverified — see below.) |
| Can the browser call these directly (skip the proxy)? | **No** — no CORS on Yahoo, and MAYA 403s a foreign `Origin`. A server-side proxy (Edge Function) is required regardless. |

## Part A — what a Supabase Edge Function can do

Supabase Edge Functions run on **Deno** (TypeScript/JavaScript) on Deno Deploy
infrastructure. Relevant to us:
- **Outbound `fetch()` to arbitrary HTTPS: yes** — this is the core capability
  we need (proxy Yahoo/SEC/MAYA).
- **Timeouts/limits:** per-invocation wall-clock and CPU-time limits (order of
  tens to a few hundred seconds depending on plan/config), ~256 MB memory,
  cold-start on first hit. Fine for a few quick HTTP calls per search; *confirm
  the exact current numbers in the Supabase docs at build time.*
- **No headless browser:** the sandbox has no Chromium, cannot spawn
  subprocesses, and has no writable filesystem for browser binaries →
  **Playwright/Chromium is impossible.** Plain HTTP calls = yes; cookie-harvest
  via a real browser = no.

So the question that decides everything is Part C: does MAYA need the browser?

## Part B — US + global search (plain GETs, trivially portable) ✅

Both are ordinary HTTPS GETs; they port to an Edge `fetch()` directly. Only
gotcha is headers:

| Source | Endpoint | Works | Needs |
|---|---|---|---|
| Global | `query1.finance.yahoo.com/v1/finance/search?q=…` | ✅ 200 | a browser-ish `User-Agent` (**no UA → HTTP 429**); no key |
| US | `www.sec.gov/files/company_tickers.json` | ✅ 200, 10,408 entries (~800 KB) | a **descriptive** `User-Agent` with contact (**no/blank UA → HTTP 403**) |

- Yahoo returns the same EQUITY-filterable candidates we validated in 4a
  (`SAP` → `SAP`, `SAP.DE`, `SAP.TO`, …).
- The SEC map is a **static ~800 KB file** — an Edge Function should fetch it
  **once and cache** (KV / a Supabase table / in-memory across warm
  invocations), not re-download per keystroke.

## Part C — THE key question: MAYA without Playwright ✅ (with one caveat)

**Finding: MAYA search works over a plain HTTPS GET with browser-like headers
and NO harvested Incapsula cookie.** Tested cold (fresh, zero cookies sent):

| Endpoint | Result |
|---|---|
| `apicontent.tase.co.il/api/search/market?q=טבע&culture=he-IL` | **HTTP 200**, real JSON (`data[0] = {id:"629", name:"טבע", category:"חברה", url:…}`) |
| `apicontent.tase.co.il/api/search/market?q=629014` | **HTTP 200**, `{id:"629014", name:"טבע", category:"מניות", …}` |
| `maya.tase.co.il/api/v1/companies/autocomplete?search=טבע` | **HTTP 200**, `[{type:"COMPANY", key:629, value:"טבע תעשיות…"}]` |
| `maya.tase.co.il/api/v1/companies/604/details` | **HTTP 200**, full company JSON (incl. `mainSecurityId`) |
| `maya.tase.co.il/api/v1/reports/breaking-announcement` | **HTTP 200**, real announcements |

These hosts **are** behind Imperva — responses carry `x-cdn: Imperva`,
`x-iinfo: …`, and set `incap_ses_*` cookies — **but the gate does not challenge
the API GET requests.** They return 200 + JSON on the first request with no
prior cookie, stably across repeated passes. In other words, the Playwright
cookie-harvest we built in Phase 2b is **not required for the search/lookup API
itself**; a plain server-side `fetch()` (i.e. an Edge Function) is enough.

**Header requirements (important):**
- Send `User-Agent`, `Accept`, `Accept-Language: he-IL`, `Referer:
  https://maya.tase.co.il/`.
- **Do NOT forward the browser's `Origin`.** Adding `Origin: https://example.com`
  makes Imperva return **403**. The proxy must omit `Origin` (or set a
  MAYA-appropriate `Referer`) — which a server-side Edge Function controls and a
  browser cannot.

**The one caveat — datacenter IP (unverified):** all probes ran from a
**residential IP**. Imperva classifies by IP reputation and challenges
datacenter/cloud egress ranges more aggressively; Supabase Edge Functions run
from datacenter IPs. So there is a real (untested) risk that the same cold GET
returns a **403 JS challenge from an Edge Function's IP** even though it's 200
from here. This is the *same* IP-reputation risk we flagged in 2b — except 2b
proved a *Playwright* harvest passes from GitHub Actions; it did **not** test a
plain browserless GET from a datacenter. **Before relying on live Edge→MAYA,
this must be verified with a ~10-minute throwaway Edge Function deploy** (fetch
`search/market?q=טבע`, check 200 vs 403).

**No lighter/static fallback needed to answer Part C** — the plain GET works.
(If it turned out gated from Edge, the robust fallback is the pre-cache in the
recommendation below.)

## Why an Edge Function (proxy) is required at all — CORS

The browser can't call these endpoints directly:
- **Yahoo** returns **no `Access-Control-Allow-Origin`** → a browser `fetch()`
  can't read the response (CORS-blocked).
- **MAYA** returns **403** when a foreign `Origin` is present (browsers always
  send their real `Origin`).
- Both Yahoo and SEC need controlled `User-Agent` values a browser can't set.

A server-side proxy (Edge Function) sits between the UI and these APIs: it sets
the right headers, omits `Origin`, adds CORS headers for our own app, and can
rate-limit/cache. So the Edge Function is needed regardless of the Israel
question.

## Recommendation for 4b architecture

**One Supabase Edge Function `search(q)` that proxies all three markets,
browserless:**
- **Global → Yahoo** live (`query1…/search`, EQUITY filter, UA set). Must be
  live — the world is too big to pre-cache. Resolve-assisted per 4a.
- **US → SEC** map, fetched once and **cached** (800 KB static file), matched
  in-function; or fold into the same Yahoo call.
- **Israel → MAYA** `search/market` + `companies/autocomplete` live plain GET
  (headers as above, no `Origin`). **Gate this on the 10-min datacenter-IP
  verification.**

**De-risking Israel (pick based on the verification):**
- **(a) Preferred if Edge→MAYA passes from datacenter IP:** call MAYA live in
  the Edge Function — uniform live search for all markets, no browser, no
  pre-cache.
- **(b) Robust fallback (and arguably better UX regardless):** a **scheduled
  job pre-caches a searchable `tase_securities` table** (name ↔ security number
  ↔ companyId) into Supabase; the Edge Function/UI searches that table
  instantly. This removes *all* live-gate/IP/rate risk for Israel, gives the
  fastest Hebrew typeahead (no per-keystroke MAYA call), and the collector can
  now populate it **browserlessly** (plain GET) — or with Playwright on GitHub
  Actions as today. Israel is a bounded universe (a few thousand securities),
  so pre-caching is cheap and complete.
- **(c) Not recommended:** limiting Israeli search to already-onboarded/seeded
  securities — too restrictive for "add any security".

**Bottom line:** US + global live via the Edge proxy is certain. MAYA is
browserless-capable (big result — no Playwright needed for search), so Israel
can also be live via the same Edge Function **if** the datacenter-IP check
passes; otherwise (or for best typeahead speed) pre-cache a `tase_securities`
table on a schedule and search that. Recommended 4b build order: Edge proxy for
US+global first, verify Edge→MAYA from a throwaway deploy, then either wire MAYA
live or add the pre-cache table.
