# Browserless TASE securities enumeration — Phase 2c-4b-1 (Step 1)

**Date:** 2026-07-14 · **Question:** how to enumerate MANY tradeable TASE
securities (name ↔ security number ↔ companyId ↔ type) **browserlessly**, to
fill a searchable `tase_securities` table. All probes: plain HTTPS GET,
browser-like headers, **no `Origin`**, no cookie harvest (per
EDGE_SEARCH_FINDINGS.md). Throwaway scripts.

## What does NOT work (no clean single broad-list endpoint)

- `api/v1/companies` / `companies/all` / `securities` (guessed list endpoints) → **403**.
- `companies/autocomplete?search=` (empty) → **403**; single Hebrew char → **400** (min length).
- `market.tase.co.il/he/market_data/securities/data/all` → **200 but it's the
  Angular SPA HTML shell**, not data (WAF page — as documented in Phase 0).
- `mayaapi.tase.co.il/...`, `api.tase.co.il/...` → **403**.

So there is **no one-shot JSON dump** of all securities reachable browserlessly.

## What DOES work (two browserless enumeration methods)

Both confirmed live, cold, no cookie:

1. **companyId sweep** — `GET api/v1/companies/<id>/details` returns, per
   company: Hebrew `name`, `mainSecurityId` (the primary stock's security
   number, or null for bond-only/no-stock), `isBond`, `isDeleted`, and a
   `secrities[]` array (every listed security + its `securityType`). Valid IDs
   cluster (low IDs 404; e.g. 604=לאומי→604611, 813=סנו→813014,
   2543=אודיסייט→1239185). Complete but ~2,700 requests to cover the range.
2. **autocomplete prefix enumeration** — `GET api/v1/companies/autocomplete?
   search=<prefix>&take=50` returns `[{type:"COMPANY", key:<companyId>,
   value:<Hebrew name>}]`. **10 two-char prefixes → 395 unique companyIds.** A
   curated prefix set covers most *active* companies in far fewer requests
   (popular prefixes cap at `take`, so coverage is broad but not guaranteed
   exhaustive).

Neither gives the security number directly from the enumeration: autocomplete
yields companyId+name, and the **security number = `mainSecurityId`** still
comes from one `companies/<id>/details` call per company (same call the
onboarding engine already uses in `resolve_company_to_primary_stock`).

## Chosen method (gentle + grows over time)

**Autocomplete prefix enumeration → per-company `details` → upsert**, because
it is much lighter than the full ID sweep (~a few hundred requests vs ~2,700)
and the ToS asks for gentle polling. Concretely, `collect_tase_list.py`:

1. Enumerates companyIds via a curated Hebrew 2-char prefix list (`+ take=50`),
   de-duped → `{companyId: name}`.
2. **Always includes the watchlist**: every TASE security on any user's
   watchlist and its `maya_company_id` (already stored on `securities`) is
   force-included, so the search set is never missing what we already track.
3. For each companyId, `companies/<id>/details` → `mainSecurityId` (the PRIMARY
   STOCK security number), the main security's `securityType`, and `isBond`.
   Bond-only / no-stock / deleted companies are skipped (no primary stock).
4. Upserts one row per company's primary stock into `tase_securities`
   (`ON CONFLICT(security_number) DO UPDATE`), paced ~0.15s/request, fail-soft.

**Coverage is broad but not guaranteed 100%** (autocomplete `take` cap +
curated prefixes). That's acceptable and matches the task's blessed fallback:
the set **grows over time** (prefixes can be tuned; onboarding adds any security
it resolves; live MAYA search in 4b-2 covers the long tail). Israel is a bounded
universe (~hundreds of active companies), so this converges quickly.

**Nothing special was needed** for the browserless path from this machine:
plain `urllib` GET with `User-Agent` + `Accept-Language: he-IL` + `Referer:
https://maya.tase.co.il/`, **no `Origin` header** (a foreign `Origin` → 403),
no cookies. Imperva is in front (`x-cdn: Imperva`) but does not challenge these
API GETs.
