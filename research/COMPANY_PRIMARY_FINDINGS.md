# Company name → primary stock resolution — Phase 2c-1b

**Date:** 2026-07-13 · **Closes the 2c-1 OPEN ISSUE:** a company NAME search now
resolves to the company's PRIMARY STOCK (ordinary share) instead of a dead
company row. Bonds / other series are still added by typing their exact
security number (unchanged). Backend only. Console Hebrew was mojibake on
Windows; true names shown here.

## Step 1 — endpoint investigation (reliable path FOUND)

Driving a MAYA company page (`/he/companies/<companyId>`) in headless Chromium
and logging its XHRs revealed:

- **`GET api/v1/companies/<companyId>/details`** — the company profile. Key
  fields:
  - **`mainSecurityId`** — MAYA's own designated **primary/ordinary share**
    security number. This is the whole answer.
  - `isBond` (bool) — bond-issuing entity.
  - `isDual` (bool) — dual-listed (e.g. Teva).
  - `secrities` [sic] — array of every listed security with `securityId`,
    `securityType`, `securityName`, `isTradable`, `marketCap`, … (bonds,
    options, warrants, RSUs, plus the share).
- The page independently confirmed the mapping by auto-fetching
  `api/v1/tradingdata?securityId=<mainSecurityId>` (604611 for Leumi, 629014
  for Teva, 822015 for Dan Hotels).

### `mainSecurityId` is authoritative (verified)

| companyId | company | mainSecurityId | expected security # | match |
|---|---|---|---|---|
| 604 | Bank Leumi (32 securities: share + 30 bonds/other) | 604611 | 604611 | ✅ |
| 662 | Bank Hapoalim (28 securities) | 662577 | 662577 | ✅ |
| 629 | Teva (dual-listed) | 629014 | 629014 | ✅ |
| 822 | Dan Hotels | 822015 | 822015 | ✅ |
| 813 | Sano | 813014 | 813014 | ✅ |
| 2093 | Bio-Dvash | 1082346 | 1082346 | ✅ |
| 815 | (no-stock company) | **null** | — | ✅ not-resolvable |

The Bio-Dvash row is the important cross-check: its security number (1082346)
has **no** arithmetic relation to its companyId (2093), yet `mainSecurityId`
returns it exactly — so this is a real lookup, not a coincidence.

In every company that has a stock, `mainSecurityId` points at the **ordinary
share** (`securityType` = "מניה רגילה", `isTradable=true`), even for banks with
30+ bond series and for dual-listed Teva. No fragile Hebrew type-parsing is
needed — MAYA already computes "the main security."

### The picking rule (documented)

```
primary_stock(company) =
    mainSecurityId        if mainSecurityId is not null AND not isBond
    None (NOT-RESOLVABLE) otherwise   # bond-only issuer / nothing listed
```

`None` → the company is surfaced as **NOT-RESOLVABLE-BY-NAME** with a hint to
enter a security number. We never guess a series. Dual share classes are
handled for free: MAYA's `mainSecurityId` is the designated primary, so we
don't have to choose between classes ourselves.

## Step 2 — build

- `desk/maya_client.py`: added `COMPANY_DETAILS_URL`.
- `desk/onboarding.py`:
  - `resolve_company_to_primary_stock(company_id) -> int | None` — GET details,
    return `mainSecurityId` (or None for bond-only/no-stock), fail-soft.
  - `_maya_suggest()` rewritten: direct `מניות` rows still become
    security-number suggestions; **company rows now resolve to their primary
    stock** and become resolvable suggestions (`hint="TASE stock (company
    primary)"`). Companies with no primary stock are included as
    not-resolvable (`hint="company has no primary stock — enter a security
    number"`, empty identifier). Bounded to `MAX_COMPANY_RESOLUTIONS=6` extra
    `/details` calls per suggest.
  - `suggest()` ranking/dedup unchanged except empty-identifier company rows
    dedupe by name so several distinct no-stock companies survive.
- Exact security-number and US-symbol paths are **untouched**.

## Verify — results (live)

`resolve_company_to_primary_stock`: 604→604611, 662→662577, 629→629014,
815→None — all correct.

**suggest "בנק לאומי"** → 3 suggestions, first is the resolvable primary stock:
```
[TASE] 604611   בנק לאומי       (TASE stock (company primary))
[TASE] (none)   בנק לאומי-...    (company has no primary stock — enter a security number)
[TASE] 593038   <other company> (TASE stock (company primary))
```
`suggest → resolve` handoff: picking 604611 → `resolve("TASE","604611")`
returns a valid ResolvedSecurity (see caveat below).

**suggest "טבע"** → `629014 טבע` (direct) first, then no-stock companies.
**suggest "דן מלונות"** → `822015 דן מלונות` (direct) + another company's
primary `1103852` + no-stock companies. Multiple, never auto-picked.

**Bond-only / no-stock** → the bare term "בנק" returns bank-related entities
that genuinely have no primary stock (companyIds 922/968/…), all surfaced with
the not-resolvable hint. Clean, no crash, no guess. (Note: MAYA's search ranks
these ahead of the major banks for the bare term; a specific name like
"בנק לאומי" surfaces the real bank — matching MAYA's own search UX.)

**Regressions (unchanged):**
```
TASE 813014  -> Sano   | SANO.TA | manual     (NaN trap intact)
TASE 1242882 -> Bagira | BGRA.TA | yfinance
US   AAPL    -> Apple Inc. | AAPL | yfinance
```

## Caveat (pre-existing, not introduced here)

Resolving a **newly-discovered** TASE stock by number (e.g. Leumi 604611, not
in our seed) returns `price_source='manual'` with `yahoo_symbol=None`, because
there's no free security-number → Yahoo-letter-ticker source (documented in
2c-1 / Phase 0). The name→primary-stock resolution itself is correct and
complete; supplying the letter ticker (or a DataHub mapping) to lift such
stocks onto the `yfinance` tier is a separate, already-tracked item.

## Verdict

The OPEN ISSUE is **closed**. A company name now resolves to its primary stock
via MAYA's authoritative `mainSecurityId`; companies without a stock return a
clean not-resolvable-by-name message; exact-number and US paths are unchanged.
