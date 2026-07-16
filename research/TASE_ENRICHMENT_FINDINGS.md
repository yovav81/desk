# TASE enrichment gap — Phase 6 step 1 investigation

**Date:** 2026-07-16 · **Scope:** investigation only, no code. MEASURED /
DOCUMENTED / INFERRED / UNKNOWN marked throughout.

## The headline, up front

**The gap is not missing wiring — it is a missing DATA SOURCE.** A UI-added
TASE security lacks exactly one thing the price collector needs: the **Yahoo
letter ticker** (`LUMI` for 604611). No code in this repo can derive it:
`tase_securities` never has it, MAYA doesn't provide it, and yfinance rejects
`604611.TA` (MEASURED in Phase 0). The seeded securities work because **a human
typed the ticker into `data/securities.csv`**. The TODO's framing — "run
maya_ids + onboarding in cron" — is **stale**: neither tool resolves a ticker,
so running them in cron would leave Leumi exactly as manual as it is now.

---

## A. What a UI-added TASE security looks like

### A1. The UI insert path (MEASURED)
Candidate built in **`web/src/useSearch.js:60-80` (`taseCandidate`)**, written by
**`web/src/useWatchlist.js` `add()`** via
`supabase.from('securities').upsert({...}, {ignoreDuplicates: true})`.
Columns written: `sec_id, symbol, name, asset_type, market, price_source,
yahoo_symbol, maya_company_id`.

For a TASE pick the candidate is built from a `tase_securities` row, and
**`tase_securities.symbol` is always NULL** (the sweep in
`collect_tase_list.py` never writes it — MEASURED by grep; the column comment
says "if known (else NULL)"). So `hasTicker` is always false and the insert is:

| column | value for 604611 |
|---|---|
| sec_id / symbol | `604611` / `604611` (number stands in for the missing ticker) |
| name | `בנק לאומי לישראל בע"מ` ✅ |
| market / asset_type | `TASE` / `stock` ✅ |
| **price_source** | **`manual`** |
| **yahoo_symbol** | **NULL** |
| maya_company_id | populated from `tase_securities.company_id` ✅ (so **MAYA filings already work** for UI-added securities) |

### A2. What collect_prices requires (MEASURED)
`securities` columns (db.py): `sec_id, symbol, name, asset_type, market,
price_source, yahoo_symbol, maya_company_id, cik`. The auto tier requires:
1. on someone's watchlist, 2. **`price_source == 'yfinance'`**, 3.
`resolve_yahoo_symbol(symbol, market, yahoo_symbol)` yielding a ticker Yahoo
actually has (explicit `yahoo_symbol`, else `symbol + ".TA"`).

### A3. The blocker (MEASURED)
**The single blocking datum is the letter ticker.** `price_source='manual'` is
the *consequence*, not the cause: flipping it to `yfinance` alone would make the
collector fetch `604611.TA`, which yfinance 404s (MEASURED, Phase 0
research/FINDINGS.md — numeric `.TA` tested and rejected; the NaN guard would
then mark it `no_data`). Leumi's real symbol is `LUMI.TA` (MEASURED — priced in
Phase 0). Nothing in the DB or any wired source maps 604611→LUMI.

### A4. Read-only query for the side-by-side (you run it)
```sql
select s.sec_id, s.symbol, s.name, s.price_source, s.yahoo_symbol,
       s.maya_company_id, q.source as quote_source, q.status as quote_status,
       q.last_price, q.as_of
from public.securities s
left join public.quotes q on q.sec_id = s.sec_id
where s.market = 'TASE'
order by s.price_source, s.sec_id;
```
Reading it: **enriched** rows have a letter `symbol` ≠ sec_id (TEVA, BGRA,
DANH) and `price_source='yfinance'`; **UI-added gap** rows have
`symbol = sec_id`, `yahoo_symbol NULL`, `price_source='manual'`,
`quote_status='no_data'`; **legitimately manual** rows (SANO, BDVSH) have a
letter symbol but `price_source='manual'` *and real prices* (`status='ok'`)
from `manual_prices`.

## B. What already exists

### B1. `desk/onboarding.py` (MEASURED — read end to end this session)
`suggest()/resolve()/add_to_db()`. For TASE, `_resolve_tase(number)` resolves
**name** (MAYA search) and **maya_company_id** (2-hop), then takes the ticker
**only from the existing DB row** (`existing.get("symbol")/("yahoo_symbol")`) —
if the row's symbol is the bare number, there is nothing to find, the NaN guard
fails, and it resolves `price_source='manual'`. **It cannot derive a ticker it
was never given.** This is by documented design ("no free number→ticker
source"; no-guess policy).

### B2. `desk/maya_ids.py` (MEASURED)
Resolves security number → **maya_company_id only** (2-hop, idempotent,
`maya_company_id IS NULL` filter). Never touches ticker/price_source. For
UI-added securities it's mostly redundant — the UI already writes `company_id`
from `tase_securities`.

### B3. `desk/seed.py` — why TEVA and Bagira work (MEASURED)
`seed()` upserts `data/securities.csv` rows verbatim. The CSV rows are
`629014,TEVA,...,yfinance,` and `1242882,BGRA,...,yfinance,` — **the letter
ticker and tier were human-curated in the file**. collect_prices then does
`resolve_yahoo_symbol("TEVA","TASE",None)` → `TEVA.TA` → prices. The "working
path" is not an enrichment pipeline; it's manual data entry at seed time.

### B4. Existing CLI (MEASURED)
`python -m desk.onboard_cli resolve TASE <number> [--add]` exists — but per B1
it would resolve 604611 to `price_source='manual'` again. **The TODO 4b-3 note
claiming it "resolves the ticker … and can upgrade manual→yfinance" is wrong
for numbers-only securities** — it upgrades only when the DB row already
carries a usable symbol.

## C. Options for the fix

**Precondition for ANY option: a number→letter-ticker source.** Candidates:
1. **TASE DataHub "Securities (Basic)"** — DOCUMENTED free (signup + API key,
   research/FINDINGS.md §5; still PENDING since Phase 0). The official,
   complete mapping. Cost: your signup + a small fetch-and-cache job.
2. **Yahoo search by security number** (`query1…/search?q=604611`) — **UNKNOWN.**
   Digits are routed to MAYA today by *design choice*, not because Yahoo was
   shown to fail. A 10-minute probe would settle it. Risk if it works:
   collision-safety (the GLOBAL lesson — valid-but-wrong matches with clean
   prices), mitigated by the number being a much tighter key than a name.
3. **MAYA** — no endpoint observed to return an English/Yahoo ticker
   (DOCUMENTED, COMPANY_PRIMARY_FINDINGS.md). Dead end as of that research.

### The three proposed shapes, assessed honestly
| Option | Cost | Failure modes | Verdict |
|---|---|---|---|
| **1. Self-healing collector step** (cron finds NULL-enrichment rows, enriches) | A new small collector + cron step; ~zero marginal runtime (bounded query, few rows) | Without a ticker source it **enriches nothing that matters** — it would fill `maya_company_id` (already filled by the UI) and stop. With a source: per-security network calls need the NaN guard + no-guess fallback; a flaky source could flap rows | ✅ **The right shape — but only after a ticker source exists** |
| **2. One-shot CLI run manually** | Near-zero (onboard_cli exists) | Can't resolve tickers either (B4); and you've explicitly rejected manual steps | ❌ |
| **3. Enrich in the Edge Function at insert** | Re-implement MAYA 2-hop + ticker resolution + price validation in Deno | Violates the stated design rule; **the yfinance NaN guard cannot run in Deno** (it's a Python library), so no-guess validation would be lost or reinvented; slow inserts | ❌ — the design rule holds, no argument to break it |

### Recommendation
**Two-step:** first a short probe step (Yahoo-by-number; if it fails, DataHub
signup) to establish the ticker source — **then build option 1**: a small
idempotent enrichment collector in the existing cron that, for TASE rows with
`yahoo_symbol IS NULL`, resolves number→ticker via the verified source,
validates with the existing NaN guard (`collect_prices.closes_series`), and
upgrades `manual→yfinance` only on real prices (`add_to_db`'s never-downgrade
merge already models this). Skip-and-log when the source has no answer — the
row **stays honestly manual** (D). Option 1 without the source first would be
motion without progress.

## D. Which securities are genuinely un-enrichable?

**Decision rule (already in the code, MEASURED):** enrichable ⇔ a real Yahoo
ticker exists **and** `_yfinance_has_prices()` returns non-NaN closes. Sano
(SANO.TA) and Bio-Dvash (BDVSH.TA) have tickers but junk/no data — the NaN
guard correctly leaves them `manual`, priced by hand via `manual_prices`.
That's the "skip, don't fabricate" rule working as designed; any enrichment
collector must reuse it verbatim.

**Can the UI distinguish "awaiting enrichment" from "manual forever" today?
No (MEASURED).** Both render the `ידני` tag; the only visible difference is
downstream of `quotes.status`: never-enriched rows show `no_data` (all dashes),
while curated-manual rows with entered prices show values. But
`manual + no_data` is ambiguous — it also matches a legit-manual security whose
prices just haven't been entered yet. **No DB field encodes the semantic
difference**; encoding it (e.g., an `enrichment_attempted`/`no_source` marker,
or inferring "forever" from a failed enrichment pass) is a design decision for
the build step, not made here.

---

# YAHOO SEARCH-BY-NUMBER PROBE (2026-07-16)

**Scope:** probe only, per Step 1's open question. 20 HTTP requests total
(18 search + 2 price checks), 1s spacing, via the existing
`onboarding._yahoo_search` / `_yfinance_has_prices` helpers — no new HTTP
client. Probe script: `research/yahoo_by_number_probe.py` (untracked scratch).

## Verdict: **VIABLE-WITH-GUARD — but only via the constructed ISIN.**
The bare number and `<number>.TA` forms fail **0/6**. The **ISIN form resolves
6/6 with provable identity** — and as a bonus it exposed that Phase 0's
"no free source" verdict for Sano and Bio-Dvash was an artifact of testing
wrong, human-guessed tickers.

## 1. Query forms tested (MEASURED)

1. **Bare number** (`604611`) → NO EQUITY HITS, all 6. (Step 1's assumption
   that digits-to-MAYA routing was a design choice, not a tested failure, is
   now settled: Yahoo genuinely cannot search by TASE number.)
2. **Number + `.TA`** (`604611.TA`) → NO EQUITY HITS, all 6. Consistent with
   the Phase 0 yfinance 404.
3. **Constructed ISIN** — TASE ISINs embed the security number:
   `IL + zfill(9)(number) + Luhn check digit`. Construction validated OFFLINE
   against two known ISINs before any request (Apple `US0378331005`, Teva
   `IL0006290147` — both check digits reproduced), then live 6/6. Chosen
   because an ISIN is the ISO 6166 **globally unique identifier of the
   security itself** — a hit cannot be a name collision.

## 2. Results (MEASURED, one line per security)

| Number | Our name | ISIN queried | Yahoo returned | Yahoo's name | Exchange | Verdict |
|---|---|---|---|---|---|---|
| 604611 | בנק לאומי לישראל | IL0006046119 | **LUMI.TA** | BK LEUMI LE ISRAEL | Tel Aviv | **MATCH** |
| 1176593 | נקסט ויז'ן מערכות מיוצבות | IL0011765935 | **NXSN.TA** | NEXT VISION STABIL | Tel Aviv | **MATCH** (discovered) |
| 629014 | טבע (control) | IL0006290147 | **TEVA.TA** | TEVA PHARMA IND | Tel Aviv | **MATCH** ✅ control |
| 1242882 | Bagira (control) | IL0012428822 | **BGRA.TA** | BAGIRA M HLDGS LTD | Tel Aviv | **MATCH** ✅ control |
| 813014 | סנו (neg. control) | IL0008130143 | **SANO1.TA** | SANO-BRUNO'S ENTER | Tel Aviv | **MATCH — see §3** |
| 1082346 | ביו דבש (neg. control) | IL0010823461 | **BHNY.TA** | BEEIO HONEY LTD | Tel Aviv | **MATCH — see §3** |

Name verification (task §3): judged by me against `securities.name` — Bank
Leumi, Next Vision *Stabilized* Systems, Teva, Bagira, Sano-Bruno's
Enterprises (= סנו), Beeio *Honey* (= ביו דבש, dvash = honey). 6/6 are the
same companies. All six returned on the Tel Aviv exchange. No WRONG-COMPANY
results, no extraneous first hits.

## 3. The "negative controls" — flagged as instructed, and what they actually mean

Per the task rule, a symbol returned for Sano/Bio-Dvash is a red flag —
**so here is the flag, examined:** the method returned SANO1.TA and BHNY.TA.
These are **not fabrications**: the names match the companies exactly, the
ISIN is identity-by-construction, and — checked with the existing NaN guard
(`_yfinance_has_prices`) — **both have real, non-NaN closes** (MEASURED).

The resolution of the contradiction: **Phase 0 never tested these symbols.**
It tested `SANO.TA` and `BDVSH.TA` — human-guessed tickers from the seed CSV —
found junk, and concluded "no free source exists." The real listings are
**SANO1.TA** and **BHNY.TA**. The negative controls were false negatives of
the *old guessing method*, not of this one. Consequence (beyond this probe):
**Sano and Bio-Dvash are liftable from the manual tier to yfinance** once
their symbols are corrected — the "legitimately manual" set may be empty
today. (The `manual_prices` mechanism stays valuable for genuinely unlisted
cases, e.g. bonds.)

## 4. Why this dodges the Reliance failure mode (INFERRED, from structure)

The Reliance trap is a **text-search** problem: a name/ticker query returns a
plausible wrong company with clean prices, and no downstream check catches it.
An ISIN query is not a text search — the ISIN *contains the security number*,
and ISO uniqueness means whatever listing Yahoo returns for `IL0006046119` is
security 604611 or nothing. Identity is proven by the query itself, not
inferred from the response.

## 5. The guard, specified (for the build step — not built here)

1. Query Yahoo **only** by constructed ISIN (never bare number, never name).
2. Require the hit's **exchange == Tel Aviv** (drops any dual-listing
   surprise, e.g. Teva's NYSE line).
3. Take the first EQUITY hit only; **zero hits → stay manual, log, never
   guess** (the existing no-guess rule).
4. Downstream, unchanged: the yfinance **NaN guard** decides
   `yfinance` vs `manual` on real closes — identity and price-availability
   remain separate questions.
5. Log Yahoo's name next to `securities.name` for human eyeballing (a
   Hebrew↔English name comparison is a sanity belt, not an automatable hard
   gate — the structural ISIN check is the gate).

## 6. Honest limits

- **n = 6.** All matched, including two symbol discoveries, but six securities
  are not the whole exchange. The guard exists precisely so an eventual miss
  degrades to "stays manual," never to a wrong company.
- The ISIN construction (`IL + zfill(9) + Luhn`) is validated on 2 known ISINs
  + 6 live round-trips — DOCUMENTED format for Israeli ISINs, but a TASE
  security with a non-conforming ISIN would simply resolve to nothing (safe
  direction).
- Price levels (ILA agorot ÷100) are the collector's job as always — nothing
  here touches conversion.
- DataHub remains the *authoritative* fallback if ISIN search ever proves
  flaky at scale; nothing in this result requires the signup today.

---

# ISIN RESOLUTION SCALE TEST (2026-07-16)

**Scope:** the Step 2 method, unchanged, against a random 50-security sample.
Probe only. 50 Yahoo requests, 1.0s spacing, **zero fetch failures / no
throttling** (a log-capture distinguished HTTP failures from genuine empties —
none occurred). Script: `research/isin_scale_test.py`; raw TSV in scratch.
One stated deviation from Step 2: `retries=0`, so throttling could not silently
double the request budget.

## Headline numbers (MEASURED)

| Metric | Count | Rate |
|---|---|---|
| **MATCH — correct company, Tel Aviv listing (usable)** | **46/50** | **92.0%** |
| Right company, WRONG EXCHANGE (Camtek → NASDAQ line; guard rejects → stays manual) | 1/50 | 2% |
| NO-HIT (stays manual, safe) | 3/50 | 6% |
| **WRONG-COMPANY** | **0/50** | **0%** |

**Verdict against the stated thresholds: 92% with 0 WRONG-COMPANY → the
"80–95%" band: viable WITH a manual fallback for the remainder.** Not the
≥95% tier — not rounding up. Fallback cost: the ~8% unresolved stay exactly
where they are today (`manual`, ידני tag) until a human supplies a ticker (CSV
edit) or DataHub does. Every failure fails SAFE — no fabrication, no wrong
identity.

## Sample (reproducible)

- **Population:** the LOCAL `desk.db` snapshot of `tase_securities` — **439
  rows, 2026-07-14 vintage** (`DESK_DB_URL` is not available in my
  environment; the live table has 557). **Stated bias:** the ~118 rows the
  snapshot lacks are the long-tail companies only the fuller companyId sweep
  found — plausibly the Yahoo-poorest names. **The true rate at 557 could be
  somewhat lower than 92%.** MEASURED on 439-population; INFERRED direction of
  bias.
- **Selection:** `random.Random(20260716).sample(rows, 50)` over rows ordered
  by `security_number` — seed **20260716**, reproducible.
- **Composition:** 44 מניה רגילה, 5 יחידת השתתפות, 1 מנית ריט; all
  `is_primary_stock=1` (the table only holds primary stocks). No overlap with
  Step 2's six.
- **Name-matching rule (stated):** a HIT counts as MATCH only if Yahoo's
  English name corresponds to our Hebrew name by translation or
  transliteration judged by me, anchored on distinctive tokens (e.g. מגדלי ים
  תיכון ↔ MEDITERRANEAN TOWERS, ביכורי השדה ↔ BIKUREY HASADE). Two rows
  required judgment and are flagged in the table; neither is doubtful enough
  for AMBIGUOUS. No fuzzy scoring was used — every row was read.

## Full results (50 rows, MEASURED; verdicts judged per the rule above)

| # | Number | Our name | ISIN | Yahoo symbol | Yahoo name | Exch | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | 1143619 | רני צים | IL0011436198 | RANI.TA | RANI ZIM SHOPPING | TLV | MATCH |
| 2 | 1097260 | ביג | IL0010972607 | BIG.TA | BIG SHOPPING CENTE | TLV | MATCH |
| 3 | 1094119 | קמהדע | IL0010941198 | KMDA.TA | KAMADA LTD | TLV | MATCH |
| 4 | 1156926 | ג'נריישן קפיטל | IL0011569261 | GNRS.TA | GENERATION CAPITAL | TLV | MATCH |
| 5 | 1139864 | רציו פטרוליום | IL0011398646 | RTPT.TA | RATIO PETROLEUM | TLV | MATCH |
| 6 | 1095264 | קמטק | IL0010952641 | **CAMT** | Camtek Ltd. | **NASDAQ** | **RIGHT-COMPANY / WRONG-EXCHANGE** |
| 7 | 1175934 | קיסטון אינפרא | IL0011759342 | KSTN.TA | KEYSTONE INFRA LTD | TLV | MATCH |
| 8 | 1142454 | אלמור חשמל | IL0011424541 | ELMR.TA | ELMORE ELEC INSTL | TLV | MATCH |
| 9 | 777037 | שופרסל | IL0007770378 | SAE.TA | SHUFERSAL LTD | TLV | MATCH |
| 10 | 280016 | קסטרו | IL0002800162 | CAST.TA | CASTRO MODEL | TLV | MATCH |
| 11 | 1166974 | משק אנרגיה | IL0011669749 | MSKE.TA | MESHEK ENGY RENEWA | TLV | MATCH |
| 12 | 716019 | מטעי הדר | IL0007160190 | CTPL1.TA | I.C.P. ISRAEL CITR | TLV | MATCH (translation: citrus plantations) |
| 13 | 1172618 | ביכורי השדה | IL0011726184 | BKRY.TA | BIKUREY HASADE GR | TLV | MATCH |
| 14 | 1139955 | מדיפאואר | IL0011399552 | — | — | — | **NO-HIT** (see below) |
| 15 | 1174846 | יוניק-טק | IL0011748469 | UNTC.TA | UNIC-TECH LTD | TLV | MATCH |
| 16 | 1183813 | אימאג'סט אינט' | IL0011838138 | ISI.TA | IMAGESAT INERTNATI | TLV | MATCH |
| 17 | 1084953 | סינאל מלל | IL0010849532 | SNEL.TA | SYNEL PAYWAY M.L.L | TLV | MATCH |
| 18 | 1242650 | בסט קבוצה | IL0012426503 | BSTG.TA | BST GROUP EAW LTD | TLV | MATCH |
| 19 | 576017 | חברה לישראל | IL0005760173 | ILCO.TA | THE ISRAEL CORP | TLV | MATCH |
| 20 | 1181569 | סופרין | IL0011815698 | SFRN.TA | SUFRIN HLDGS LTD | TLV | MATCH |
| 21 | 1175090 | שור-טק השקעות | IL0011750903 | STEC.TA | SURE TECH INVS | TLV | MATCH |
| 22 | 1134139 | קנון הולדינגס | IL0011341398 | — | — | — | **NO-HIT** (see below) |
| 23 | 660019 | ניסן | IL0006600196 | NISA.TA | NISSAN | TLV | MATCH (judged: bare "NISSAN", TLV listing + ISIN anchor it — the Israeli company, not the automaker) |
| 24 | 278010 | וילק | IL0002780109 | WILK.TA | WILK TECHNOLOGIES | TLV | MATCH |
| 25 | 1157114 | יוטרון | IL0011571143 | UTRN.TA | UTRON LTD | TLV | MATCH |
| 26 | 1173228 | ווישור גלובלטק | IL0011732281 | WESR.TA | WESURE GLOBAL TECH | TLV | MATCH |
| 27 | 1131523 | מגדלי ים תיכון | IL0011315236 | MDTR.TA | MEDITERRANEAN TOWE | TLV | MATCH |
| 28 | 1140573 | מניבים ריט | IL0011405730 | MNRT.TA | MENIVIM REIT | TLV | MATCH |
| 29 | 1187640 | פרקומט | IL0011876401 | PRKM.TA | PARKOMAT INTERNATI | TLV | MATCH |
| 30 | 1129493 | תומר אנרגיה | IL0011294936 | TOEN.TA | TOMER ENERGY | TLV | MATCH |
| 31 | 1119833 | גלוב אנרגיה-ש | IL0011198335 | — | — | — | **NO-HIT** (see below) |
| 32 | 1168962 | אלמדה ונצ'רס | IL0011689622 | AMDA.TA | ALMEDA VENTURES LI | TLV | MATCH |
| 33 | 180018 | אנגל שלמה | IL0001800189 | ANGL.TA | SALOMON A.ANGEL | TLV | MATCH |
| 34 | 1224641 | גבאי קבוצה | IL0012246414 | GABY.TA | GABAY GROUP CONSTR | TLV | MATCH |
| 35 | 1180686 | טונדו סמארט | IL0011806861 | TNDO.TA | TONDO SMART LTD | TLV | MATCH |
| 36 | 1081843 | מיטב בית השקעות | IL0010818438 | MTAV.TA | MEITAV INV HOUSE | TLV | MATCH |
| 37 | 288019 | סקופ | IL0002880198 | SCOP.TA | SCOPE METALS GROUP | TLV | MATCH |
| 38 | 723007 | נורסטאר החזקות | IL0007230076 | NSTR.TA | NORSTAR HLDGS INC | TLV | MATCH |
| 39 | 1098565 | רבוע כחול נדל"ן | IL0010985658 | BLSR.TA | BLUE SQUARE REAL | TLV | MATCH |
| 40 | 1105022 | תיגבור קבוצה | IL0011050221 | TIGBUR.TA | TIGBUR GROUP LTD | TLV | MATCH |
| 41 | 1080753 | אילקס מדיקל | IL0010807530 | ILX.TA | ILEX MEDICAL | TLV | MATCH |
| 42 | 1083856 | אלארום טכנ' | IL0010838568 | ALAR.TA | ALARUM TECHNOLOGIE | TLV | MATCH |
| 43 | 1188622 | פרופדו | IL0011886228 | PRPD.TA | PROPDO LTD | TLV | MATCH |
| 44 | 1233493 | עומר הנדסה | IL0012334939 | OMCN.TA | OMER CONST & ENGI | TLV | MATCH |
| 45 | 440016 | כרמית | IL0004400169 | CRMT.TA | CARMIT CANDY IND | TLV | MATCH |
| 46 | 351015 | חד-אסף | IL0003510158 | HOD.TA | HOD-ASSAF INDS | TLV | MATCH (judged: חד/HOD transliteration variance; אסף/ASSAF + industries anchor it) |
| 47 | 1175561 | ביונ תלת מימד-ש | IL0011755613 | BYON-M.TA | BEYON 3D LTD | TLV | MATCH (תלת מימד = 3D) |
| 48 | 1187962 | קרסו נדל"ן | IL0011879629 | CRSR.TA | CARASSO REAL ESTAT | TLV | MATCH |
| 49 | 1080928 | אינטר תעשיות | IL0010809288 | ININ.TA | INTER INDUSTRIES | TLV | MATCH |
| 50 | 1083443 | גולדן אנרג'י | IL0010834435 | GLDE.TA | GOLDEN ENERGY | TLV | MATCH |

## The failures, examined individually (task §5)

All four non-MATCHes are **real TASE listings** — none looks unlisted. So these
are method limits, not junk securities; each has a diagnosis:

1. **קנון הולדינגס (1134139) — NO-HIT, structural.** Kenon Holdings is
   incorporated in **Singapore** (DOCUMENTED — dual-listed NYSE/TASE); its real
   ISIN starts `SG`, so a constructed `IL…` ISIN is **inapplicable by
   construction**, not merely unindexed. The method can never resolve
   foreign-incorporated TASE issuers.
2. **מדיפאואר (1139955) — NO-HIT, probably the same class.** The name
   ("Medipower (Overseas)") suggests foreign incorporation — **INFERRED**, not
   verified. Alternatively a Yahoo coverage gap.
3. **גלוב אנרגיה-ש (1119833) — NO-HIT, cause UNKNOWN.** A participation unit;
   two other participation units in the sample resolved fine (RTPT, TOEN), so
   the type alone isn't the explanation. Likely a Yahoo coverage gap for an
   illiquid name.
4. **קמטק (1095264) — right company, wrong listing.** Camtek is dual-listed
   (TASE + NASDAQ, one ISIN). Yahoo returned **only** the NASDAQ line
   (`CAMT`, 1 equity hit total — the `.TA` line wasn't in the response at
   all, so "pick the TLV hit among several" cannot recover it). Contrast:
   TEVA — also dual-listed — returned `TEVA.TA` first in Step 2. **Dual-listed
   ordering/coverage is unreliable; the Tel-Aviv-exchange gate is
   load-bearing** and correctly rejects this into `manual` rather than
   importing USD prices into a TASE row.

## What this changes in the Step 2 guard — nothing, and that's the point

Every failure mode lands in the guard's safe branch (no TLV hit → stays
manual, log). The guard as specified in Step 2 §5 survives contact with the
sample unchanged. 0/50 wrong companies means the structural identity argument
(ISIN ⊇ security number) held in practice, not just in theory.

## Honest limits

- **92% is on the 439-row snapshot, not the live 557.** The missing ~118
  long-tail rows are plausibly Yahoo-poorer; treat 92% as an upper-ish
  estimate until the enrichment collector reports real numbers in production.
- Name verdicts are my judgment per the stated rule (2 judged rows flagged
  inline). An automated pipeline gets identity from the ISIN + TLV gate, and
  logs names for eyeballing — same conclusion as Step 2.
- n=50 puts a rough ±8% confidence band on the rate; the qualitative finding
  (failures are safe and mostly structural) is the robust part.
