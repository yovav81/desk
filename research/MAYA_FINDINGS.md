# MAYA breaking-announcements feed — Phase 2b pre-check (investigation only)

**Date:** 2026-07-13 · **Scope:** verify we can fetch MAYA company disclosure
**announcements** (headlines + links + dates) for DESK. Independently
rediscovered from a live browser session — no field/financial codes, no app
collector, nothing wired into `collect.yml`. Throwaway scripts live under
`research/` (`maya_*.py`); data artifacts (`maya_*.json`, `*.png`) are
gitignored.

## TL;DR verdicts

| Question | Answer |
|---|---|
| Cookie-harvest + JSON replay works today? | **Yes.** Headless Chromium passes the Incapsula/Imperva bot gate; the resulting cookies replay in a plain `requests.Session`. |
| Announcements endpoints | `GET api/v1/reports/breaking-announcement` (site-wide) and `POST api/v1/reports/companies` (per company). |
| security number → companyId | **Automatable, but 2 hops** via the public search endpoint. No single "number→id" call exists. Resolve once, cache per security. |
| All 5 test companies resolved? | **Yes — 5/5**, with recent announcements fetched. |
| Run location | **Cookie harvest must run somewhere that passes the bot gate.** JSON replay is cheap. Actions is *plausible but not guaranteed* (bot-gate IP sensitivity) — see below. |

## Reproduce (environment)

```
pip install playwright
python -m playwright install chromium
```

Then: `maya_harvest.py` (harvest cookies + log XHRs) → `maya_resolve3.py`
(number→companyId) → `maya_final.py` (per-company announcements) →
`maya_docs.py` (verify document URLs). All read `maya_cookies.json` written by
the harvest step.

## 1. Cookie harvest + gate

Headless Chromium (realistic desktop UA, `locale=he-IL`,
`Accept-Language: he-IL`, `--disable-blink-features=AutomationControlled`,
`navigator.webdriver` masked) loads `https://maya.tase.co.il/`, waits for
network idle. The gate **passes**: the site sets Imperva/Incapsula cookies
(`visid_incap_*`, `incap_ses_*`), Dynatrace (`dtCookie`, `rxVisitor`), and app
cookies. Page title renders (Hebrew "מאיה - בורסה..."), not a challenge page.

The SPA's own XHRs revealed the API surface. The load fires, among others:

- `GET api/v1/reports/breaking-announcement?limit=5` ← **site-wide announcements feed**
- `GET api/v1/corporate-actions/upcoming?limit=5`
- search widget attributes exposed `content/api/search/market` (→ 301 →
  `apicontent.tase.co.il/api/search/market`)

## 2. JSON replay (plain requests, no browser)

Harvested cookies + matching headers (UA, `Accept-Language: he-IL`,
`Referer: https://maya.tase.co.il/`) replay cleanly. **Header gotcha:** sending
`Content-Type: application/json` on a **GET** trips a 403 from the WAF — set it
only on POSTs.

### Site-wide feed — `GET api/v1/reports/breaking-announcement?limit=N`

Returns a JSON **array**; each announcement:

| field | meaning |
|---|---|
| `id` | report id (e.g. `1756347`) |
| `title` | headline (Hebrew) |
| `publishDate` | ISO datetime, e.g. `2026-07-13T17:29:02.88` |
| `isPriority` | breaking/priority flag |
| `reporterId` | reporting companyId |
| `companies[]` | `{companyId, name, mainSecurityId, isDual, ...}` |
| `attachments[]` | `{fileType (htm/pdf1/pdf2), fileName, url}` |

### Per-company feed — `POST api/v1/reports/companies`

Body: `{"pageNumber":1,"companyId":629,"limit":20,"offset":0}` (needs
`Content-Type: application/json`). Returns the **same announcement shape** as
the array above, newest first. This is the endpoint a DESK collector would poll
per watchlisted TASE company.

### Document / detail URLs (verified HTTP 200)

- Attachment file: `https://mayafiles.tase.co.il/` + `attachments[].url`
  (e.g. `.../rhtm/1750001-1751000/H1750872.htm`, `.../rpdf/.../P…-00.pdf`).
  Note: the host is **mayafiles.tase.co.il**, not maya.tase.co.il (the latter
  403s for these paths).
- Human report page: `https://maya.tase.co.il/reports/details/<report id>`.

## 3. security number → MAYA companyId (the key unknown)

**There is no direct number→id endpoint.** MAYA's search
(`GET apicontent.tase.co.il/api/search/market?q=<term>&culture=he-IL`, param is
`q`) behaves differently for numbers vs names:

- `q=<security number>` → returns **only** the `מניות` (stock) row, whose `id`
  is the security number itself and whose URL points at market.tase.co.il — **no
  companyId**. It does, however, carry the **official company name**.
- `q=<company name>` → returns `חברה`/`דיווחים` rows whose `id` **is the
  companyId**, with URL `…/he/companies/<companyId>` /
  `…/reports/companies?companyId=<companyId>`.

**Working recipe (fully automatable, 2 hops):**
1. `search/market?q=<security number>` → read the official name from the row
   whose `id == <security number>`.
2. `search/market?q=<that name>` → take the `id` of the row whose URL contains
   `/companies/` → that's the **companyId**.

A cleaner name→id alternative also works:
`GET api/v1/companies/autocomplete?search=<name>&take=8` returns items shaped
`{"type":"COMPANY","key":<companyId>,"value":..,"label":..}` — the **`key`** is
the companyId. (Still name-keyed, so hop 1 above is still needed to get the
name from a number.)

**Rejected shortcut:** the companyId is *not* reliably the security number
without its last 3 digits. It coincides for older listings (Teva 629014→629,
Sano 813014→813, Dan Hotels 822015→822) but **breaks** for newer/small caps
(Bio-Dvash 1082346→**2093**, Bagira 1242882→**2547**). Do not use the prefix
trick — always resolve via search.

**Recommendation for the collector:** resolve each watchlisted TASE security's
companyId **once** and cache it (e.g. a `maya_company_id` column on
`securities`); re-resolve only when null. Per-poll cost is then just the POST
feed call per company.

## 4. Resolution results — all 5 test companies

| Security # | Name | MAYA companyId | Method |
|---|---|---|---|
| 629014 | טבע (Teva) | **629** | number→name→search |
| 813014 | סנו (Sano) | **813** | number→name→search |
| 1082346 | ביו דבש (Bio-Dvash) | **2093** | number→name→search |
| 1242882 | בגירה (Bagira) | **2547** | number→name→search |
| 822015 | דן מלונות (Dan Hotels) | **822** | number→name→search |

**5/5 resolved.** Sample recent announcements (via `POST reports/companies`),
document = `mayafiles.tase.co.il/` + path:

- **Teva (629)** — `2026-06-22` "אירוע דיווח פנימי/ד.מהותי-FORM 4-Shields
  Matthew" · doc `rhtm/1750001-1751000/H1750872.htm`
- **Sano (813)** — `2026-07-05` "מכתב עדכון לבעלי מניות…" · doc
  `rhtm/1753001-1754000/H1753941.htm`
- **Bio-Dvash (2093)** — `2026-07-07` "דיווח מיידי…30.6.26" · doc
  `rhtm/1754001-1755000/H1754975.htm`
- **Bagira (2547)** — `2026-07-13` (same day) announcement · doc
  `rhtm/1756001-1757000/H1756216.htm`
- **Dan Hotels (822)** — `2026-07-01` half-year report notice · doc
  `rhtm/1753001-1754000/H1753242.htm`

(Full JSON in gitignored `research/maya_samples.json`.)

## 5. CI / run-location feasibility

- **JSON replay** (search + feeds) is trivial anywhere once you hold valid
  cookies — no browser needed, low footprint.
- **Cookie harvest** is the constraint: it needs headless Chromium *and* the
  Imperva bot gate to pass. In this local run the gate passed on a residential
  IP. From a **GitHub Actions** runner the risk is IP reputation — Imperva is
  known to challenge datacenter/cloud egress ranges more aggressively, which
  could turn the harvest into a JS/CAPTCHA challenge that headless Chromium
  won't clear.
- **Verdict:** treat as **local-friendly, Actions-plausible-but-unproven**.
  Recommended design: keep harvest and replay as separate steps; cookies live
  ~minutes–hours, so one browser harvest can feed many cheap replay polls. If
  we want Actions, prove the harvest step there first (a throwaway workflow) or
  budget for a fallback (residential proxy, or run the harvest on the same box
  that already runs local tooling and push only the JSON to the DB).

## Fragility / ToS caveats (record before building)

- **Undocumented private API.** `api/v1/...` is the SPA's own backend, not a
  published product — shapes/paths can change without notice. Pin to the
  fields we use (`id`, `title`, `publishDate`, `companies[].companyId`,
  `attachments[].url`) and fail soft.
- **Bot gate is the single point of failure.** If Imperva tightens, the whole
  chain stops at harvest. Keep the Playwright harvester isolated and easy to
  swap.
- **No login/secrets** — this is a public gate, so nothing sensitive is stored;
  cookies are short-lived and disposable (kept out of git).
- **Politeness/ToS:** poll gently (the site is a public regulatory feed, but
  scraping ToS applies). Cache companyIds, use small `limit`, space out polls,
  set a truthful-ish desktop UA. Reuse cookies rather than re-harvesting each
  cycle.
- **Two hosts in play:** `maya.tase.co.il` (SPA + `api/v1` + report pages),
  `apicontent.tase.co.il` (search, CMS), `mayafiles.tase.co.il` (documents).
  All three must remain reachable.

## Bottom line for Phase 2b

The full path is proven end-to-end on live data: **harvest cookies → resolve
each TASE security's companyId once (cache it) → poll `POST reports/companies`
per company → store `id/title/publishDate/detail+doc URLs`.** Dedup naturally
on report `id` (or the `reports/details/<id>` URL, mirroring `news.url`). The
open engineering risk is purely *where the cookie harvest runs*; the data
mechanics are solved.
