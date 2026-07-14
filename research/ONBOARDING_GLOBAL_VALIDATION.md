# Global onboarding resolver — Phase 2c-4a validation

**Date:** 2026-07-14 · **Scope:** extend `desk/onboarding.py` with a
Yahoo-search-backed GLOBAL resolver (resolve-assisted, never auto-pick) + GBp
(London pence) handling in `collect_prices`, alongside the unchanged US (SEC)
and TASE (MAYA) paths. Results from a live CLI run
(`research/onboarding_global_validate.py`). Console Hebrew was mojibake on
Windows; true names shown here.

## Global suggest — collisions surface multiple, never auto-picked ✅

- **`SAP`** → 14 candidates. Includes US `SAP` (SAP SE) **and** the global
  listings `SAP.DE` (XETRA), `SAP.TO` (Saputo — different co.), `SAP.JO`
  (Sappi — different co.), `7811.KL`, etc. The required `SAP.DE` is present;
  the collisions are all shown for the user to pick — none auto-selected.
- **`Reliance`** → US `RS` (Reliance Steel, US) **and** `RELIANCE.NS` (Reliance
  Industries, India) both present, plus other NSE/BSE Reliance entities. The
  correct company is **not** auto-picked — exactly the safeguard the pre-check
  demanded (Yahoo ranked `RS` first, but it's just one candidate in the list).
- **`Toyota`** → `TM` (US ADR) + `7203.T` (Tokyo) + Frankfurt listings.
- **`Nestle`** → `NESN.SW` (Swiss primary) first + ADR/other venues.
- **`ASML`** → US `ASML` + `ASML.AS` (Amsterdam) + others.

Routing: Latin queries hit **US (SEC) AND global (Yahoo)**, merged and de-duped
by bare symbol (US wins over its GLOBAL twin, e.g. one `SAP`), so distinct
collisions (`SAP.DE`/`SAP.TO`) all survive.

## EQUITY filter ✅ (Yahoo path)

- **`VOO`** (Vanguard ETF) → **0 candidates** — Yahoo's ETF row is filtered
  (`quoteType!='EQUITY'`) and it isn't in the SEC registry.
- **`SPY`** → Yahoo's SPY **ETF** row is filtered from the GLOBAL results.
  (Note: SPY still appears as a **US** candidate because the SEC
  `company_tickers` registry lists the SPDR trust as a registrant — that's the
  pre-existing US/SEC path, not the global EQUITY filter, which works.)

## Global resolve — chosen Yahoo symbol ✅

| market/id | symbol | price_source | currency | name |
|---|---|---|---|---|
| GLOBAL/`SAP.DE` | SAP.DE | yfinance | **EUR** | SAP SE |
| GLOBAL/`7203.T` | 7203.T | yfinance | **JPY** | Toyota Motor Corp |
| GLOBAL/`NESN.SW` | NESN.SW | yfinance | **CHF** | Nestlé N |
| GLOBAL/`ASML.AS` | ASML.AS | yfinance | **EUR** | ASML Holding |
| GLOBAL/`HSBA.L` | HSBA.L | yfinance | **GBP** ← from GBp | HSBC Holdings PLC |
| GLOBAL/`ZZZZ.ZZ` | — | — | — | **NotFound** (no usable price data) |

`market='GLOBAL'`, `maya_company_id=None`, `yahoo_symbol=` the chosen symbol.
A bad/nonexistent symbol returns NotFound — no guess.

## London pence (GBp) trap — handled like agorot ✅

- `resolve('GLOBAL','HSBA.L')` records **`currency='GBP'`** (native `GBp`
  normalized to the major unit). resolve does **not** touch prices — the ÷100
  happens in `collect_prices`.
- End-to-end in `collect_prices` (verified directly):
  - `currency_for('HSBA.L', None)` → `GBp` (fresh, from `fast_info`).
  - `currency_for('HSBA.L', 'GBP')` → `GBp` (cached round-trip via the `.L`
    suffix — mirrors `.TA`→`ILA`), so re-runs keep converting.
  - raw last close **1462.4 GBp** → stored **14.62 GBP** (÷100), not 1462.
- `normalize_currency` map: `ILA→ILS`, `GBp→GBP`, `GBX→GBP` (all ÷100);
  everything else passes through unscaled. This is the single place ÷100
  happens; the ILS/agorot logic is unchanged.

## Regressions — US & TASE unchanged ✅

| market/id | symbol | market | price_source | currency | note |
|---|---|---|---|---|---|
| US/`AAPL` | AAPL | US | yfinance | USD | — |
| US/`TEVA` | TEVA | US | yfinance | USD | dual-listed → US ADR (as before) |
| TASE/`629014` | TEVA | TASE | yfinance | ILS | Teva TASE |
| TASE/`813014` | SANO | TASE | manual | ILS | Sano → manual (NaN trap intact) |

## Hebrew routing ✅

- `suggest('טבע')` → 4 TASE candidates via MAYA, **no Yahoo 400, no crash**.
  Yahoo search is never called with Hebrew (guarded + routed to MAYA).

## Minor notes (not blockers; for the 4b UI step)

1. **Merged-suggest ranking.** SEC name-substring matching adds noise for short
   queries (e.g. `SAP` matches "che**SAP**eake"), pushing `SAP.DE` past the
   top-8 in raw order (it's still in the 14 candidates). Exact-symbol matches
   rank first; finer ranking (primary listing, relevance) is a 4b UI concern.
2. **Throttle resilience.** `_yfinance_has_prices` now does one retry with a
   short pause (Yahoo throttles rapid calls); global resolves are spaced. Bulk
   collection already spaces via the collector loop.

## Verdict

Global onboarding is in and safe: Yahoo gives clean prices + candidates, the
resolve-assisted / never-auto-pick rule defuses the collision trap, the GBp
pence ÷100 mirrors agorot end-to-end, and US/TASE/Hebrew paths are unchanged.
Ready for the 4b UI (search + picker + add/remove).
