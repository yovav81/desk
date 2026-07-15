# SEC filings collector — Phase 3 step 1 investigation

**Date:** 2026-07-15 · **Scope:** investigation only, no production code written.
**Question:** how do we add an SEC EDGAR filings collector that mirrors
`collect_maya.py` and writes `filings.source='sec'` (the tag the UI already
renders)?

All EDGAR facts below are **measured** from a throwaway probe against the live
endpoints (descriptive UA, read-only GETs), not recalled from docs.

---

## 1. `collect_maya.py` contract — the pattern to mirror

| Aspect | How it works |
|---|---|
| **DB connect** | `engine = get_engine()` (reads `DESK_DB_URL`, defaults to local SQLite) then `init_db(engine)` at the top of `collect()`. No other config. |
| **Select what to poll** | `watchlisted_tase_securities(engine)`: `select(securities).join(watchlist, watchlist.c.sec_id == securities.c.sec_id).where(securities.c.market == "TASE").distinct()`. Note: **the join never references `user_id`** — it is the UNION of every user's watchlist, deliberately. |
| **Per-security gate** | Skips (never crashes) securities whose `maya_company_id` is NULL, logging a hint to run `python -m desk.maya_ids`. |
| **Fetch** | `fetch_company_reports(session, company_id)` → POST, `limit=PAGE_LIMIT` (20). Returns `None` on request failure / non-200 / bad JSON / unexpected shape — each logged as a warning; the caller `continue`s. |
| **INSERT shape** | `insert_ignore(engine, filings, ["source", "maya_id"]).values(...)` inside `with engine.begin() as conn:` — one transaction per security, one statement per row. Columns written: `sec_id` (str), `source` (`"maya"`), `maya_id` (int, from `r["id"]`), `title` (str, from `r["title"]`), `published_at` (`datetime|None`), `doc_url` (`str|None`). **`fetched_at` is left to the server default.** |
| **Dedup on re-run** | `insert_ignore` → `INSERT ... ON CONFLICT (source, maya_id) DO NOTHING`, branching on dialect inside `db.insert_ignore`. New-row counting uses `conn.execute(stmt).rowcount` (0 = already present). |
| **Malformed rows** | `if maya_id is None or not title: continue` — skip the row, keep the batch. |
| **Logging** | `logging.basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(message)s")`, logger name = module (`"collect_maya"`). Per-security: `fetched=N new=N`. Final: `done: total new filings=N`. |
| **Failure policy** | **Fail-soft, exit 0.** Gate not cleared → `log.warning(...)` + `return` (not `sys.exit(1)`). Per-company errors are warnings. The workflow must never go red because an upstream misbehaved. |
| **Non-transferable bits** | The Playwright cookie harvest + Imperva gate are MAYA-specific. SEC needs **none of it** — plain GET + a descriptive UA (§5). |

**Contract the SEC collector must copy:** union-of-watchlists selection, a
NULL-id skip with a hint, fail-soft everywhere, `insert_ignore` for dedup,
`rowcount`-based new-row counting, per-security + total logging, exit 0.

---

## 2. `filings` schema (verbatim from `desk/db.py`)

```python
filings = Table(
    "filings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("sec_id", String(32), ForeignKey("securities.sec_id"), nullable=True),
    Column("source", String(16), nullable=False),  # maya
    Column("maya_id", Integer, nullable=False),  # MAYA announcement id — dedup key
    Column("title", Text, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("doc_url", Text, nullable=True),
    Column("fetched_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    UniqueConstraint("source", "maya_id", name="uq_filings_source_maya_id"),  # dedup guard — sacred, like news.url
)
Index("ix_filings_published_at", filings.c.published_at)
```

### 🔴 The blocking schema finding

**An SEC accession number cannot go in `maya_id`.** Measured:

| | value |
|---|---|
| accession | `0001140361-26-025622` |
| digits-only as int | `114036126025622` |
| PG `INTEGER` max | `2147483647` |
| fits `INTEGER`? | **No** — overflows by ~53,000× |
| fits `BIGINT`? | yes |
| leading zeros survive? | **No** (`0001140361…` → `1140361…`) |

So `filings` **must** change before an SEC collector can dedup. Also note
`maya_id` is `nullable=False`, so an SEC row cannot simply leave it empty.
Options in §7.

---

## 3. Workflow invocation (`.github/workflows/collect.yml`)

- **Trigger:** `schedule: cron "*/15 * * * *"` + `workflow_dispatch`.
- **Concurrency:** group `desk-collect`, `cancel-in-progress: false`.
- **Runner/setup:** `ubuntu-latest` → `actions/checkout@v4` → `actions/setup-python@v5` (3.12) → `pip install -r requirements.txt` → `python -m playwright install --with-deps chromium` (for the MAYA harvest).
- **Guard:** a `Require DESK_DB_URL` step that `exit 1`s if the secret is unset.
- **Env:** `DESK_DB_URL`, `DESK_DEFAULT_USER`, `GMAIL_USER`, `GMAIL_APP_PASSWORD` from secrets.
- **Steps, in order:** `python -m desk.collect_news` → `collect_macro` → `collect_email` → `collect_prices` → `collect_maya`.
- **No `continue-on-error` anywhere** → any non-zero exit fails the run and skips later steps. This is *why* every collector is fail-soft.
- `collect_tase_list.py` is **not** here — it runs daily in `.github/workflows/tase_list.yml`.

**Implication:** an SEC step would be one more `python -m desk.collect_sec` line, appended after `collect_maya`. No new deps (stdlib/requests only — no Chromium).

---

## 4. US securities inventory — ⚠️ NOT ANSWERED (needs you)

**I could not query the DB.** `DESK_DB_URL` is not set in my environment (you
set it in yours), and I did not go looking for the credential in
`env_backup.txt` — secrets are yours to handle.

**Market values in use — confirmed from code**, not the DB: `'US'`, `'TASE'`,
`'GLOBAL'` (`securities.market`; the `db.py` comment still says `US | TASE` and
is stale since 4a added GLOBAL).

`data/securities.csv` (the **seed file only**, 7 rows — the live DB has since
diverged via onboarding) shows `US: 2` (`AAPL`, `MSFT`), `TASE: 5`.

**Please run this read-only query and paste the output:**

```sql
select market, count(*) from public.securities group by market order by 2 desc;

select sec_id, symbol, name, market, price_source
from public.securities
where market = 'US'
order by symbol;
```

Scope note: SEC coverage should be `market='US'` only. `GLOBAL` rows are Yahoo
symbols (`SAP.DE`) that do not appear in `company_tickers.json`. Foreign issuers
listed in the US **do** appear under their ADR ticker — `TEVA` → CIK `818686`
(verified) — but they file **20-F / 6-K**, not 10-K/10-Q (see §7).

---

## 5. EDGAR endpoint comparison (all measured 2026-07-15)

### Header requirement — confirmed, not assumed

| User-Agent | Result |
|---|---|
| `DESK watchlist research (contact: yovav81@gmail.com)` | **HTTP 200** |
| `python-requests/2.31.0` (generic) | **HTTP 403** |
| none | **HTTP 403** |

(Independent corroboration: `WebFetch` on `sec.gov` returned **403** — it can't
set a descriptive UA. Any SEC access must set one, exactly like
`onboarding.py`'s `SEC_UA`.)

### A. `https://data.sec.gov/submissions/CIK##########.json` ✅ recommended

CIK is **zero-padded to 10 digits** (`CIK0000320193.json`).

- **HTTP 200**, **28 KB** (gzip), **~0.44 s** for Apple.
- `filings.recent` = **1,000 filings**, covering **2015-05-29 → 2026-06-17** (Apple).
- Older filings are paged out into `filings.files[]` → `CIK0000320193-submissions-001.json` (1,236 more, 1994→2015). **Irrelevant to us** — we only want recent.
- Shape is **parallel arrays**, not a list of objects: `rec['form'][i]`, `rec['filingDate'][i]`, …

Field → our column mapping:

| `filings.recent` field | example | → our column |
|---|---|---|
| `accessionNumber` | `0001140361-26-025622` | **dedup key** (§7) |
| `form` | `8-K`, `10-Q`, `4` | filing type → filter + `title` |
| `filingDate` | `2026-06-17` | `published_at` (date only, no time) |
| `acceptanceDateTime` | `2026-06-17T18:40:43…` | better `published_at` — has a **time** |
| `reportDate` | `2026-06-15` | (period covered — not needed) |
| `primaryDocument` | `ef20073373_sd.htm` | → `doc_url` |
| `primaryDocDescription` | `FORM 4`, `SD`, `''` | → `title` (often empty/unhelpful) |
| `items` | `''` | 8-K item codes when present |
| `size`, `isXBRL`, `act`, `fileNumber`, `filmNumber`, `core_type` | | unused |

**No human-readable title exists** — this is the one real gap vs MAYA (which
gives a Hebrew headline). `primaryDocDescription` is often `''` or just `FORM 4`.
A title must be **composed** (§7).

**Document URL — construction verified HTTP 200 both ways:**
```
cik_int   = int(d['cik'])                       # 320193, NOT zero-padded here
acc_nodash= accessionNumber.replace('-', '')    # 000114036126025622
doc  = https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{primaryDocument}
index= https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{accessionNumber}-index.htm
```
The **`-index.htm`** page is the better `doc_url`: it is the human landing page
listing every document in the filing (the MAYA equivalent), whereas
`primaryDocument` can be a raw XML (`xslF345X06/form4.xml`).

### B. EDGAR browse ATOM feed per CIK ⚠️ viable but inferior

`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=&dateb=&owner=include&count=10&output=atom`

- **HTTP 200**, `application/atom+xml`, **14.2 KB for 10 entries**, ~0.63 s.
- Per `<entry>`: `<title>` = **`4 - Statement of changes in beneficial ownership of securities`** ← *a real human-readable title*, `<filing-date>`, `<filing-type>`, `<accession-number>`, `<updated>`, and `<link href>` → the `-index.htm` page.

**Pros:** the only source of a ready-made human title; link is the index page.
**Cons:** legacy `cgi-bin` (SEC steers developers to `data.sec.gov`); XML not
JSON (a parser dep or stdlib `ElementTree`); ~10× the bytes per filing
(14.2 KB/10 vs 28 KB/1000); `count` caps ~100; no bulk fields.

### C. Rate limits / pacing

- SEC's published fair-access limit is **10 req/s** across `*.sec.gov`.
- **Measured:** 10 sequential `submissions` calls in **6.1 s = 1.6 req/s**, all 200, no throttling. That is my *latency-bound* rate, **not** a limit probe — I did not try to find the ceiling.
- `Cache-Control: max-age=0, no-cache, no-store` → responses are not cacheable; every poll is a real fetch.

---

## 6. Ticker → CIK resolution

`https://www.sec.gov/files/company_tickers.json` — **HTTP 200, 779 KB, ~0.78 s**, measured:

- **10,428 entries**, shape `{"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}, …}`.
- `AAPL` → `320193`, `MSFT` → `789019`, `TEVA` → `818686`. Zero-pad for the URL: `CIK{cik:010d}`.
- **8,021 distinct CIKs / 1,468 CIKs carry >1 ticker** — e.g. CIK `1652044` → `GOOGL, GOOG, GOOGM, GOOGN`. **Ticker→CIK is many-to-one.**
- **Already in the repo:** `desk/onboarding.py` loads this exact file (`SEC_TICKERS_URL`, `SEC_UA`, `_load_sec_tickers`, `_sec_lookup_exact`) — and `supabase/functions/search/index.ts` caches it too. **Reuse, don't re-implement.**

### Column vs mapping table → **a `cik` column on `securities`**

- A security has exactly **one** issuer, so this is 1:1 with the row — same shape as the existing `maya_company_id` precedent (also an `Integer`, resolved once by `desk/maya_ids.py`, cached, skipped-with-a-hint when NULL). Mirroring it keeps one mental model.
- A mapping table would duplicate all 10,428 rows of a file SEC already hosts, to serve the ~2 securities we actually poll, and would need its own refresh job.
- **`Integer` is safe:** max `cik_str` today is **2,142,762** vs PG `INTEGER` max `2,147,483,647` — **~1,000× headroom** (measured). CIKs are assigned sequentially, so this will not overflow in any relevant timeframe.

---

## 7. Open decisions + recommendations

### D1. `filings` schema change (BLOCKING — nothing works without it)
`maya_id` is `INTEGER NOT NULL`; an accession number fits neither (§2).
- **(a) ✅ Recommended — additive, leaves the sacred guard byte-identical:** add `accession_no VARCHAR(32) NULL`, add `UNIQUE(source, accession_no)`, and **drop NOT NULL** on `maya_id`. MAYA rows keep deduping on `(source, maya_id)` with `accession_no` NULL; SEC rows dedup on `(source, accession_no)` with `maya_id` NULL. NULLs are distinct in both PG and SQLite, so neither constraint interferes with the other tier. Cost: two nullable id columns — mildly ugly, but zero risk to existing data and no backfill.
- (b) Rename to a generic `external_id VARCHAR(64)` + `UNIQUE(source, external_id)`: cleaner long-term, but rewrites the "sacred" MAYA guard and needs a backfill of `maya_id::text` — more risk for cosmetics.
- (c) Widen `maya_id` to `BIGINT` and stuff the digits in: **rejected** — loses leading zeros and abuses a column named for another source.

### D2. Which endpoint → **`data.sec.gov/submissions/CIK##########.json`**
Modern documented API, JSON not XML, one 28 KB call per company covers years of
filings, and carries every field we need. The ATOM feed's only advantage is a
ready-made title, which D3 solves cheaply.

### D3. `title` (NOT NULL) composition
No human title in the JSON. **Recommend** a small static `FORM_TITLES` map for
the forms we keep (`8-K` → `דוח אירוע (8-K)` or English — a UI/language call for
you), falling back to `primaryDocDescription`, then bare `form`. ~15 entries,
static, no network. *(Alternative: fetch the ATOM feed just for titles — a
second request per company for cosmetics. Not recommended.)*

### D4. Which forms to keep → **allowlist, not blocklist**
Measured on Apple's 1,000 recent: **`4` = 589 (59%)**, `8-K` 104, `424B2` 48,
`144` 44, `10-Q` 33, `PX14A6G` 27, `FWP` 24, `SC 13G/A` 22, `DEFA14A` 12,
`SD` 11, `3` 11, `DEF 14A` 11. **Unfiltered, the feed is ~60% insider Form 4
noise.**
- **Recommend keep:** `10-K`, `10-Q`, `8-K`, `DEF 14A`, **`20-F`, `6-K`** (foreign private issuers — **TEVA files these, not 10-K/10-Q**), plus `/A` amendments of each.
- **Ignore:** `3`/`4`/`5`, `144`, `424B*`, `FWP`, `SC 13G*`, `PX14A6G`, `SD`, `DEFA14A`.
- An allowlist fails safe: an unknown new form type is skipped, never spam.

### D5. Polling frequency → **the existing 15-min `collect.yml`**
Consistency with `collect_maya` beats a new schedule; 8-Ks are the time-sensitive
ones. Cost is trivial today (~2 US securities × 28 KB). **Pace ~0.2 s between
companies** (≈5 req/s, half the published 10 req/s fair-access limit). If the US
list grows past ~50, revisit (50 × 96 runs/day ≈ 4,800 req/day ≈ 134 MB) — a
`filings`-freshness gate like `quotes.anchors_date` would be the fix.

### D6. CIK backfill → **`python -m desk.sec_ids`, mirroring `desk/maya_ids.py`**
A one-shot idempotent CLI: for `market='US'` securities with `cik IS NULL`,
resolve `symbol` → CIK via the cached `company_tickers.json` (reusing
`onboarding._load_sec_tickers`) and `UPDATE securities SET cik = …`. Re-runs
resolve 0. `collect_sec` then **skips NULL-cik securities with a hint** — exactly
how `collect_maya` treats a NULL `maya_company_id`. Onboarding should also set
`cik` for new US securities so the backfill stays a one-off.
*(Not in `collect.yml`: `maya_ids` isn't either — same known gap as TODO 4b-3.)*

### D7. Dedup key → **`(source, accession_no)`**, with a caveat to accept
The accession number is globally unique per filing, so this mirrors
`(source, maya_id)` exactly.
- **Known limitation:** because ticker→CIK is many-to-one (§6), if **two of our
  securities share a CIK** (e.g. `GOOGL` + `GOOG` → `1652044`), the first
  `sec_id` inserted wins and the second security's detail page shows **no
  filings**. `collect_maya` already has this exact flaw (two TASE securities of
  one company share a `companyId`).
- Alternative `(source, accession_no, sec_id)` would store the filing once per
  security and fix that, at the cost of duplicate rows in the shared feed.
- **Recommend (source, accession_no) now** — matches the established pattern and
  neither GOOG nor GOOGL is on the watchlist. Revisit if two same-CIK securities
  are ever added.

### D8. Stale comment (trivial)
`db.py`'s `securities.market` comment says `# US | TASE`; the real domain has
been `US | TASE | GLOBAL` since 4a. Worth fixing when the `cik` column lands.

---

## What must happen before coding

1. **You:** run the §4 query so we know the real US inventory.
2. **Decide D1** (schema) — everything else is downstream of it.
3. **Decide D3** (title language) — Hebrew or English form labels in the feed.
