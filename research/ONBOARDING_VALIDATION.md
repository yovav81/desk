# Onboarding engine validation — Phase 2c-1

**Date:** 2026-07-13 · **Scope:** validate `desk/onboarding.py` (backend only,
no UI) — `suggest`, `resolve`, `add_to_db` — over a deliberately tricky set.
Facts below are from a live run (`research/onboarding_validate.py`) against
SEC + MAYA + yfinance. Console Hebrew showed as mojibake on Windows; names
here are the true UTF-8 values.

## resolve() — all correct

| market | id | price_source | yahoo_symbol | maya_company_id | ccy | name / reason |
|---|---|---|---|---|---|---|
| US | AAPL | yfinance | AAPL | – | USD | Apple Inc. |
| US | MSFT | yfinance | MSFT | – | USD | MICROSOFT CORP |
| US | TEVA | yfinance | TEVA | – | USD | TEVA PHARMACEUTICAL INDUSTRIES LTD |
| TASE | 822015 | yfinance | DANH.TA | 822 | ILS | דן מלונות (Dan Hotels) |
| TASE | 1242882 | yfinance | BGRA.TA | 2547 | ILS | בגירה (Bagira) |
| TASE | 629014 | yfinance | TEVA.TA | 629 | ILS | טבע (Teva TASE) |
| TASE | 813014 | **manual** | SANO.TA | 813 | ILS | סנו (Sano) |
| TASE | 1082346 | **manual** | BDVSH.TA | 2093 | ILS | ביו דבש (Bio-Dvash) |
| US | ZZZZZ | – | – | – | – | NotFound: US ticker 'ZZZZZ' not in the SEC registry |
| TASE | 999999 | – | – | – | – | NotFound: TASE security '999999' not found on MAYA and not already known |

Key outcomes:
- **US clean** (AAPL, MSFT): resolve to `yfinance` via SEC exact-ticker + a
  yfinance non-NaN price check.
- **Dual-listed TEVA**: `resolve US TEVA` correctly returns the **US ADR**
  (SEC title, `yahoo_symbol=TEVA`), distinct from the TASE line
  (`resolve TASE 629014` → `TEVA.TA`). Both coexist; the caller chooses market.
- **TASE clean** (Dan Hotels, Bagira, Teva TASE): `yfinance`, `.TA` letter
  ticker, `currency=ILS` (agorot ÷100 stays in `collect_prices`, not here).
  Bagira is a recent IPO — resolve still succeeds; the short-history handling
  lives in `collect_prices` (period returns NULL "since first date").
- **NaN trap** (Sano 813014, Bio-Dvash 1082346): the two that must **not**
  produce a junk price. `SANO.TA` / `BDVSH.TA` return no yfinance data, so the
  shared NaN guard (`collect_prices.closes_series`) sends them to
  `price_source='manual'` — never a fabricated price. This is the single most
  important thing 2c-1 had to get right, and it does.
- **Garbage** (ZZZZZ, 999999): clean `NotFound` with a reason, no crash.

### TASE letter-ticker note (design fact)
yfinance does **not** accept numeric `.TA` symbols (`629014.TA` → 404); only
letter tickers work (`TEVA.TA`, `DANH.TA`). There is no free
security-number → letter-ticker source (TASE is WAF-blocked, per Phase 0). So
`resolve(TASE, <number>)` gets the letter ticker from the **known mapping**
(an existing `securities` row / `yahoo_symbol` override). A brand-new TASE
security not yet in the mapping resolves by name + companyId but falls back to
`price_source='manual'` until a ticker/override is supplied — consistent with
the no-guess policy.

## suggest() — mostly correct, one open gap

| query | kind | result |
|---|---|---|
| `Apple` | US name | 6 suggestions (Apple Inc. AAPL first, then APLE, PAPL, …) ✅ multiple, no auto-pick |
| `AAPL` | US symbol | 1 suggestion (exact) ✅ |
| `טבע` | Hebrew name | 1 suggestion (629014 טבע) — the only tradeable (`מניות`) row MAYA returned |
| `ZZZZZ` | US symbol | 0 suggestions ✅ (→ NotFound on resolve) |
| `999999` | IL number | 0 suggestions ✅ (→ NotFound on resolve) |
| `בנק` | Hebrew name | **0 suggestions** ⚠️ — see OPEN ISSUE |

## OPEN ISSUE — company-vs-security suggest gap (for a follow-up step)

MAYA's `search/market` returns two kinds of rows for a name query:
- `מניות` (tradeable security) rows — carry a **security number**, directly
  onboarding-able. `suggest()` currently returns only these.
- `חברה` (company) rows — carry a **companyId**, not a security number.

For a **specific** name (`טבע`) MAYA returns a `מניות` row, so suggest works.
For a **generic sector term** (`בנק` = "bank") MAYA returns only `חברה` /
delisted-company rows (banks 922, 968, …) and **no** `מניות` rows in the
(≈10-row, company-prioritized) response — so `suggest('בנק')` yields 0.

This is **not** a resolve/no-guess failure (we correctly refuse to fabricate a
security number). It's a suggest-coverage gap: to surface generic-term matches
we'd need a **company → security-number** hop (e.g. companyId → its main
security / `mainSecurityId`). That path was deliberately **not built here** —
it's a separate decision. Options to weigh next:
- add a company→security lookup so `חברה` rows become resolvable suggestions;
- or have the 2c-2 UI show company matches and expand a company to its
  securities on selection;
- or accept that generic terms require a specific company name / the security
  number directly.

Until then: onboarding by **security number** and by **specific name** works;
generic Hebrew sector terms may return nothing (no crash, no guess).

## add_to_db() — idempotent ✅

Every resolved security: first call `inserted` or `updated` (filling
previously-NULL `yahoo_symbol`/`maya_company_id` on rows seeded earlier),
second call **`unchanged`**. The merge never downgrades a good row
(`yfinance`→`manual` is refused; set fields are not clobbered with NULL).

```
US    AAPL      -> first=updated    second=unchanged
US    MSFT      -> first=updated    second=unchanged
US    TEVA      -> first=inserted   second=unchanged
TASE  822015    -> first=updated    second=unchanged
TASE  1242882   -> first=updated    second=unchanged
TASE  629014    -> first=updated    second=unchanged
TASE  813014    -> first=updated    second=unchanged
TASE  1082346   -> first=updated    second=unchanged
```

## Verdict

The hard core is validated: US + TASE resolution, the manual-fallback NaN
trap, clean NotFound on garbage, and idempotent persistence all behave. The
only follow-up is the company-vs-security suggest gap above — tracked as an
open issue, not a blocker for the engine itself.
