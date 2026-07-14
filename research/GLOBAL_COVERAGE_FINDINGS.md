# Global equity coverage via yfinance — Phase 2c pre-check (investigation only)

**Date:** 2026-07-14 · **Question:** before extending onboarding beyond US +
Israel, can we (a) RESOLVE a plain user query to the right global Yahoo symbol,
and (b) get CLEAN price data for major world exchanges? Free sources only
(yfinance + Yahoo's public search endpoint). No app code changed; throwaway
scripts in `research/` (`global_*.py`), only this `.md` committed.

## TL;DR verdicts

| Question | Answer |
|---|---|
| Clean price coverage on major exchanges? | **Yes — 10/10** exchanges returned a full ~1y of non-NaN daily closes. |
| Yahoo search good enough for global autocomplete? | **Partly.** Great candidate lists for Latin-script names, but ranking is unreliable and same-ticker collisions surface **valid-but-wrong companies**. Safe as an *assisted picker* (show candidates, user chooses), **not** as an auto-pick. |
| Hebrew name resolution via Yahoo search? | **No** — returns HTTP 400. Keep TASE on the existing MAYA path. |
| Currency/scale traps? | **LSE quotes in `GBp` (pence)** — a ÷100 trap identical to TASE agorot. All other tested exchanges use normal currency units. |
| Recommended scope | **Global = "resolve-assisted, exact-symbol-anchored"**: Yahoo search suggests candidates, user picks the exact symbol; price via the existing NaN guard; handle the `GBp` ÷100 case. |

## Part A — price coverage (10/10 clean)

Per-symbol `Ticker.history(period="1y")`. All returned a full year of non-NaN
daily closes (≈244–256 trading rows; gaps are just market holidays, not NaN).

| Exchange | Name | Yahoo symbol | Worked | Last close | Currency | Non-NaN rows |
|---|---|---|---|---|---|---|
| Germany XETRA | SAP | `SAP.DE` | ✅ | 137.98 | EUR | 254 |
| UK LSE | HSBC | `HSBA.L` | ✅ | **1462.0** | **GBp (pence!)** | 253 |
| Japan Tokyo | Toyota | `7203.T` | ✅ | 2839.0 | JPY | 244 |
| Hong Kong | Tencent | `0700.HK` | ✅ | 456.2 | HKD | 246 |
| France Paris | LVMH | `MC.PA` | ✅ | 478.78 | EUR | 256 |
| Switzerland | Nestlé | `NESN.SW` | ✅ | 83.78 | CHF | 250 |
| Canada TSX | Shopify | `SHOP.TO` | ✅ | 176.57 | CAD | 251 |
| Australia ASX | BHP | `BHP.AX` | ✅ | 58.71 | AUD | 255 |
| Netherlands AMS | ASML | `ASML.AS` | ✅ | 1543.0 | EUR | 256 |
| India NSE | Reliance | `RELIANCE.NS` | ✅ | 1293.0 | INR | 250 |

**Reliability trap discovered:** a first pass using `yf.download()` on all ten
in quick succession returned **empty history for every one** (Yahoo throttled
the batch) while `fast_info.currency` still resolved — a false "no data". The
retry with per-symbol `Ticker.history()` + ~2.5s spacing returned clean data
for all ten. **Lesson:** for onboarding (one symbol at a time) `Ticker.history()`
is reliable; any *bulk* fetch must space/retry or it will look like mass
"delisted/empty". (`fast_info.last_price` was flaky/None throughout — use the
last history close, not fast_info, for the price.)

## Part B — resolution (the hard part)

Endpoint: `GET https://query1.finance.yahoo.com/v1/finance/search?q=<query>`
(no key; needs a browser `User-Agent`). Returns `quotes[]` with `symbol`,
`shortname`/`longname`, `exchange`, `exchDisp`, `quoteType`. It works and is
information-rich — but turning a plain query into *the correct* symbol is where
the risk lives.

**What works:** for Latin-script names/tickers it returns strong candidate
lists spanning exchanges, with human-readable exchange names and a `quoteType`
to filter on. Examples (top hits):
- `Tencent` → **`0700.HK`** (Hong Kong, correct primary) first ✅
- `LVMH` → **`MC.PA`** (Paris, correct primary) first ✅
- `Nestle` → **`NESN.SW`** (Swiss, correct primary) first ✅
- `ASML` → `ASML` (NASDAQ) then `ASML.AS` (Amsterdam) — both correct co.
- `Toyota` → `TM` (NYSE ADR) then `7203.T` (Tokyo) — both correct co.
- `HSBC` → `HSBC` (NYSE ADR) then `HSBA.L` (London) — both correct co.

**Three real problems:**
1. **Same-ticker collisions → valid-but-WRONG company.** `SAP` returns not
   just SAP SE (`SAP.DE`/`SAP`) but also `SAP.TO` = **Saputo** and `SAP.JO` =
   **Sappi** — different companies. `Reliance` ranks `RS` = **Reliance Steel
   (US)** *first*, ahead of `RELIANCE.NS` (Reliance Industries, India). The NaN
   guard does **not** catch this — the wrong company has perfectly clean prices
   (verified: `SAP.TO` 41.45 CAD, `RS` 385.4 USD). So auto-picking the top
   result can silently onboard the wrong security.
2. **No reliable "primary listing" flag, inconsistent ranking.** Sometimes the
   US ADR ranks first (`Toyota`→`TM`, `HSBC`→`HSBC`, `ASML`→`ASML`), sometimes
   the home listing (`Nestle`→`NESN.SW`, `Tencent`→`0700.HK`, `LVMH`→`MC.PA`).
   There's no field that says "this is the primary" — you must show `exchDisp`
   and let the user choose.
3. **Mixed `quoteType`s.** `HSBC` results included an ETF and MUTUALFUND rows.
   Must filter to `quoteType == "EQUITY"` for onboarding equities.

**Hebrew fails outright:** `q=טבע` and `q=בנק לאומי` both return **HTTP 400 Bad
Request**. Yahoo search is unusable for Hebrew/Israeli names — no change to our
strategy: TASE stays on the MAYA search + companyId path we already built.

**NaN cross-check (the SANO1.TA trap):** every resolved symbol tested —
primaries, US ADRs, OTC ADRs (`NSRGY`, `TCEHY`, `LVMUY`, `ASMLF`), a Frankfurt
secondary (`TOMA.F`), and the collision tickers — returned non-NaN prices. So
for these liquid global names the NaN trap did **not** appear; the standard
NaN guard from `collect_prices` remains the right validation, but note it will
**not** protect against the wrong-company problem above (that needs user
disambiguation, not a price check).

**Rate limits / ToS:** the search endpoint served ~8 queries at ~1.2s spacing
with no throttling; it's undocumented and Yahoo's ToS discourages scraping —
use gently (debounce autocomplete, cache resolutions, realistic UA). The
*price* endpoint is the more throttle-prone one (see Part A).

## Part C — currency / scale gotchas

- **LSE pence (`GBp`) — a ÷100 trap, exactly like TASE agorot.** `HSBA.L`
  quotes `1462.0` with currency **`GBp`** (pence) = £14.62, not £1,462. yfinance
  signals it by the lowercase-`p` currency code (`GBp` vs `GBP`) — the same
  detection pattern as TASE's `ILA` vs `ILS`. Any global price display must
  divide `GBp` by 100 and show it as GBP (mirror the agorot handling in
  `collect_prices`).
- **All other tested exchanges use normal units** — EUR, JPY, HKD, CHF, CAD,
  AUD, INR — no sub-unit scaling. (Watch out for other cents-quoted venues if
  scope widens, e.g. Johannesburg `ZAc`; not needed for the majors above.)

## Bottom line — recommended scope for global onboarding

**Feasible, with guardrails.** Yahoo gives clean prices for all the major
exchanges, and the search endpoint is a good *candidate source* — but not a
safe auto-resolver.

Recommended design when we build it (next phase, not now):
1. **Resolve-assisted, never auto-pick.** Query Yahoo search, filter to
   `quoteType=='EQUITY'`, and present candidates as
   `SYMBOL — Name — Exchange` for the user to choose. This defuses the
   collision/ranking problem (RS vs RELIANCE.NS, SAP.TO vs SAP.DE) — the same
   "never auto-pick, show candidates" rule we already use.
2. **Exact-symbol path always available.** Power users type the Yahoo symbol
   directly (`SAP.DE`, `7203.T`); skip search.
3. **Validate the chosen symbol** with the existing `collect_prices` NaN guard
   (`Ticker.history()`, per-symbol, spaced) before committing it — junk symbols
   fall back to manual, same as today.
4. **Handle `GBp` ÷100** wherever prices are computed/displayed, alongside the
   existing `ILA` agorot rule; store the post-conversion currency (`GBP`).
5. **Keep TASE on MAYA** (Yahoo search can't do Hebrew); global via Yahoo,
   Israel via MAYA — two resolvers, one onboarding UI.
6. **Bulk price fetches must space/retry** (Part A throttling), or use
   per-symbol history in the collector's loop.

Net: global support is worth doing and low-risk on *pricing*; the real work is
the *resolution UX* (assisted picker + exact-symbol) and the `GBp` scale case.
