# Filing freshness — Phase 4 step 1 investigation

**Date:** 2026-07-15 (probe run 22:12 UTC) · **Scope:** investigation only. No code,
workflow, or SQL changed. SEC probed read-only (**4 requests total**); MAYA not probed.

**Every number below is labelled MEASURED / DOCUMENTED / ESTIMATED / UNKNOWN.**

---

## 🔴 Headline: the biggest freshness bug is not the polling cadence

The measured pipeline delay is 74–128 min. But **SEC filings are also being stored
with a timestamp 4 hours in the past**, and **MAYA filings ~3 hours in the future**.
Both are timezone bugs in our own parsers. In the merged, time-sorted feed a fresh
8-K therefore sorts *below* the news articles written about it — which is the exact
symptom that triggered this investigation.

> The BAC case: 8-K accepted 13:45, news at 14:04. Stored as 09:45 (4 h early), the
> filing sorts **4 h 19 min below** the news article. Polling every 60 seconds would
> not fix that. **Fixing the timezone would — for free.**

---

## A. SEC latency

### A1. What `acceptanceDateTime` represents
DOCUMENTED (SEC EDGAR APIs page, via `submissions` docs): the `filings.recent`
block carries `acceptanceDateTime` alongside `filingDate`. Acceptance is when
EDGAR *accepted* the submission; dissemination to the public feed follows.
`filingDate` is the calendar date only.

I could not retrieve a page where SEC states the *timezone* of `acceptanceDateTime`
(`sec.gov` returns **403** to the docs fetcher — no descriptive UA). So the
timezone below is **MEASURED, not DOCUMENTED**.

### A2. 🔴 `acceptanceDateTime` is Eastern time mislabelled as `Z` — MEASURED

Same filing, two endpoints, same instant, contradictory labels:

| Source | Value for accession `0000897101-26-000333` |
|---|---|
| EDGAR `getcurrent` ATOM `<updated>` | `2026-07-15T18:11:47**-04:00**` |
| `submissions` JSON `acceptanceDateTime` | `2026-07-15T18:11:47.000**Z**` |

Identical wall clock to the second; the zone labels differ by 4 h (EDT = UTC−4).
Which one is right is settled by the clock: the probe ran at **22:12 UTC**, and the
filing had *just* appeared in the "latest filings received" feed (age **0.6 min**).
`18:11 EDT = 22:11 UTC` fits; `18:11 UTC` would make it 4 h old — impossible for a
filing that just arrived. **The `Z` is wrong; the value is US/Eastern.** Matching to
the second across two endpoints rules out coincidence.

**Consequence for us (MEASURED):** `collect_sec._parse_published` does
`fromisoformat(acceptance.replace("Z", "+00:00"))` → it trusts the `Z` and stores
every SEC filing **4 hours early** (EDT; 5 h in winter EST). The probe printed this
directly: `now - acceptance = 240.6 min` for a filing 0.6 min old.

*(Not fixed here — investigation only. Note the fix is DST-dependent: EDGAR is
Eastern, so it needs a real `America/New_York` conversion, not a fixed −4 h.)*

### A3. Cache / CDN headers — MEASURED
```
Cache-Control: max-age=0, no-cache, no-store
Last-Modified: (absent)   Age: (absent)   X-Cache: (absent)
```
No caching, no CDN age, no `Last-Modified`. The JSON is generated per request, so
**no cache-imposed staleness floor** — and no header reveals an update cadence,
because there is no batch cadence to reveal (see A4).

### A4. 🟢 FLOOR on SEC latency: **under ~1 minute — MEASURED**
Three CIKs whose filings had *just* hit the acceptance-ordered feed were checked
against their `submissions` JSON:

| CIK | filing age at check | present in submissions JSON? |
|---|---|---|
| 2144867 | **0.6 min** | **YES** |
| 1934245 | **0.6 min** | **YES** |
| 1568100 | **1.0 min** | **YES** |

`submissions` is **effectively real-time**: a filing is queryable within a minute of
acceptance. **SEC imposes no meaningful floor. 100% of the SEC delay is ours** —
polling cadence (74–128 min) plus the 4 h timestamp error.

Caveat: n=3, one 2-minute window, after-hours (18:11 ET). Behaviour during a 16:00 ET
earnings flood is UNKNOWN.

---

## B. MAYA latency (the higher-value question)

### B1. What we poll and store — from the code, not a probe
- **Endpoint:** `POST https://maya.tase.co.il/api/v1/reports/companies`
  (`maya_client.REPORTS_URL`), body `{pageNumber, companyId, limit: 20, offset}`.
- **`published_at` ←** `r["publishDate"]`, via `collect_maya._parse_published`.

### B2. 🔴 MAYA timestamps are stored ~3 hours in the FUTURE — code-confirmed
```python
# e.g. "2026-07-13T17:29:02.88" (naive, Israel local) — store as-is UTC-tagged.
return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
```
`publishDate` is **naive Israel local time**, and we stamp UTC onto it. Israel is
UTC+3 (IDT), so an announcement published 17:29 local (14:29 UTC) is stored as
**17:29 UTC** — 3 h in the future.

**Consequence (ESTIMATED — follows from the code + `web/src/format.js`, not observed
in the browser):** `fmtRelative` computes `diffMin = now - d`, which is *negative*
for ~3 h, and `diffMin < 1` returns **`'עכשיו'`**. So **every MAYA filing displays
"עכשיו" for its first ~3 hours**, regardless of true age, then jumps. It also sorts
3 h too new — above genuinely newer items. The two bugs are mirror images: **SEC
reads 4 h too old, MAYA 3 h too new** — a ~7 h relative skew between the two filing
sources in one feed.

### B3. Can the floor be probed? **No — and I did not try (per constraints).**
`publishDate` is MAYA's own publication timestamp, but whether the API exposes an
announcement *before, at, or after* it appears on `maya.tase.co.il` **cannot be
derived from one API call** — it needs two clocks compared over time. Answering it
by probe would also mean hammering a public regulatory feed. **UNKNOWN.**

### B4. Cheapest measurement you could run yourself
**Option 1 — free, uses data we already have (bounds it from above).** `filings`
stores both `published_at` and `fetched_at`. Run in the SQL editor:

```sql
-- MAYA end-to-end lag. The -3h corrects the timezone bug in B2; drop it once fixed.
select sec_id, title,
       published_at, fetched_at,
       round(extract(epoch from (fetched_at - (published_at - interval '3 hours'))) / 60) as lag_min
from public.filings
where source = 'maya' and published_at is not null
order by published_at desc
limit 30;
```
This measures **source latency + cron delay combined**, so it is an *upper bound*.
Given the cron delay alone is 74–128 min, it cannot isolate MAYA's own latency —
but if `lag_min` clusters near the cron gap, MAYA's contribution is small.

**Option 2 — isolates the source (5 minutes of your time, one manual run).** Open
`maya.tase.co.il`, wait for a fresh announcement on any watchlisted company, note
the wall-clock minute it appears, then immediately run `python -m desk.collect_maya`
and compare `fetched_at`/`publishDate` for that row. One observation gives the real
answer; three gives confidence.

### B5. FLOOR on MAYA latency: **UNKNOWN**
Not measured, not documented, not honestly estimable from the code alone. **This is
the single most important open question**, because Israeli filings are the core use
case — and it is unresolved. Everything in §D about MAYA is conditional on B4.

---

## C. GitHub scheduling

### C1. `schedule` is explicitly best-effort — DOCUMENTED
From GitHub's *Events that trigger workflows*, verbatim:

> "The `schedule` event **can be delayed during periods of high loads** of GitHub
> Actions workflow runs. High load times include the start of every hour. **If the
> load is sufficiently high enough, some queued jobs may be dropped.**"

> "The shortest interval you can run scheduled workflows is once every 5 minutes."

> "In a public repository, scheduled workflows are automatically disabled when no
> repository activity has occurred in 60 days."

**No free-tier-specific throttling is documented.** GitHub attributes delay to
*aggregate* load, not account tier. Your observed **74–128 min gaps (MEASURED by
you) with a correct `*/15` cron** are consistent with the documented "delayed…
may be dropped" behaviour — but attributing it specifically to *free tier* is
**ESTIMATED**, not documented. Note our cron fires **on the hour** (`*/15` → :00,
:15, :30, :45), and `:00` is the exact moment GitHub names as worst.

### C2. Is `repository_dispatch` subject to the same queueing? **UNDOCUMENTED**
DOCUMENTED:
> "You can use the GitHub API to trigger a webhook event called
> `repository_dispatch` when you want to trigger a workflow for activity that
> happens outside of GitHub."

The "may be delayed / may be dropped" caveat is attached **only to `schedule`** —
it appears nowhere under `repository_dispatch`, and neither the events page nor the
REST endpoint page states any delay, queueing, priority, or trigger-speed guarantee.

**So the honest verdict: GitHub does *not* document that `repository_dispatch` is
exempt from queueing — it merely never claims it is subject to it.** Absence of the
warning is suggestive, not a guarantee. Anyone asserting "dispatch bypasses the
schedule queue" is stating an **ESTIMATE**. That said, the mechanism differs
materially: `schedule` is GitHub's own timer firing across every repo at :00, while
a dispatch is an inbound API call handled like a `push` — and pushes visibly do not
suffer 90-minute delays on this repo.

### C3. What an external trigger requires — DOCUMENTED
- **Endpoint:** `POST /repos/{owner}/{repo}/dispatches`
- **Token:** classic PAT / OAuth token with the **`repo` scope** (documented).
  *(Fine-grained equivalent: not captured in my fetch — **UNKNOWN**, verify before use.)*
- **Body:** `event_type` (required, ≤100 chars); `client_payload` (optional, ≤10 top-level properties, <64 KB).
- **Workflow side:** add `repository_dispatch: types: [...]` to `on:` — the workflow file must be on the default branch.
- **Free external cron:** cron-job.org (1-min granularity, free, custom POST headers/body), UptimeRobot (5-min, HTTP keyword monitor), Cloudflare Workers Cron Triggers (free tier, 1-min). All can send the POST.
- **⚠️ Security cost:** a PAT with `repo` scope — **write access to the whole repo** — must be stored in a third-party service. That is a materially worse credential posture than anything in this project today, where secrets live only in GitHub Actions. A fine-grained token would narrow it, but its required permission is unverified (above).

### C4. Realistic best-case cadence via `repository_dispatch` — ESTIMATED
Not documented; assembled from your MEASURED numbers:
- external cron jitter: 0–60 s (ESTIMATED, service-dependent)
- dispatch → runner pickup: **UNKNOWN** (undocumented; assume seconds-to-a-minute)
- **workflow wall clock: 2–4 min (MEASURED by you)** — dominated by `pip install -r requirements.txt` and `playwright install --with-deps chromium`

**Best case ≈ 3–6 min from filing to DB, at a 5-min trigger cadence** (ESTIMATED).
Versus today's 74–128 min (MEASURED). See §D2 for a cheaper way to cut the 2–4 min.

---

## D. Options — cost / benefit

### D0. Fix the two timezone bugs — **DO THIS FIRST, REGARDLESS**
- **Cost:** a few lines in two `_parse_published` functions + a one-off UPDATE to correct stored rows. No infrastructure, no credentials, no new failure mode.
- **Benefit:** removes a **4 h** error on SEC and a **3 h** error on MAYA (~7 h of relative skew). Restores correct feed ordering and makes "לפני X דק׳" mean something. **This is worth more than any scheduling change** — no cadence fixes a filing that sorts 4 h below the news about it.
- **Note:** must convert from `America/New_York` (SEC) and `Asia/Jerusalem` (MAYA) — both observe DST, so a fixed offset would break twice a year.
- ⚠️ **Verify B2 in the live UI before acting** — it's ESTIMATED, not observed.

### D1. Do nothing about scheduling
- **Cost:** filings stay 74–128 min late.
- **Benefit:** zero effort/risk.
- **Verdict:** defensible *only* if MAYA's own floor (B5) turns out to be large. If MAYA publishes in near-real-time like SEC, 90 min is all self-inflicted.

### D2. Split filings into their own lightweight workflow (`*/5` cron)
- **Cost:** one new workflow file; SEC needs no Chromium and no pandas/yfinance, so a SEC-only job skips `playwright install --with-deps chromium` → **wall clock should drop from 2–4 min toward well under 1 min** (ESTIMATED). MAYA still needs Chromium.
- **Benefit:** cuts the *run* time, and `*/5` gets more attempts through the queue — but **`schedule` is `schedule`**: the documented delay/drop applies identically. Splitting does **not** bypass throttling.
- **Verdict:** cheap and strictly positive, but it treats a symptom. Also lets prices/news keep a slow cadence they don't need to beat.

### D3. `repository_dispatch` from a free external cron
- **Cost:** a `repo`-scoped PAT living in a third-party service (**the real cost — see C3**); a new external dependency that can silently die; ~30 min setup. `workflow_dispatch`/`schedule` should be kept as a fallback.
- **Benefit:** ESTIMATED 3–6 min end-to-end vs 74–128 min — a ~20× improvement, **if** C2's estimate holds.
- **Verdict:** the only option that plausibly beats the queue, but it is built on an **undocumented** assumption and a wide credential.

### D4. Self-hosted runner
- **Cost:** a machine that must stay on; `schedule` still fires the same way.
- **Verdict:** rejected — doesn't address the trigger, only the runner.

### Recommendation

**1. Fix the timezones (D0) now.** It is the largest, cheapest, lowest-risk win, and
it is a *correctness* bug, not an optimisation. Confirm B2 in the browser first.

**2. Answer B5 before spending anything on scheduling (B4, Option 2 — 5 minutes).**
The entire scheduling question is downstream of a number we do not have. If MAYA
exposes announcements with a 30-minute lag of its own, the difference between a
90-minute and a 5-minute pipeline is far less than it looks, and D1 wins.

**3. Then, if B5 shows MAYA is fast: do D2 first** (cheap, honest, no new
credentials) and only escalate to D3 if a *measured* `*/5` split still shows
90-minute gaps. Adopt D3 as an experiment with `schedule` retained as fallback —
and treat C2 as a hypothesis to test, not a fact.

**Do not do D3 first.** It trades a documented, well-understood limitation for an
undocumented behaviour plus a repo-wide credential in a third-party service — to
fix a delay that is currently smaller than our own timestamp error.

---

# MAYA TIMESTAMP RE-EXAMINATION (2026-07-16)

Triggered by production evidence that appeared to contradict the Step 2 model
(above), on the back of which I shifted ~120 MAYA rows by -3h. This section
re-derives the model from scratch. **The earlier section is left intact as the
record of what we believed and why.**

**Verdict up front: the MAYA half of sql/003 was NOT a mistake, and the 09:10 row
is NOT corrupted — it is correct, and was almost certainly misread. The
"contradiction" is not one: the observation offered as refuting the Step 2 model
is in fact the strongest available PROOF of it.** Reasoning and residual doubt below.

## 1. What the conversion function actually does — traced, not assumed

Current `desk/collect_maya.py::_parse_published` (MEASURED — quoted verbatim):

```python
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        # The normal case: naive == Israel local.
        dt = dt.replace(tzinfo=MAYA_TZ)
    return dt.astimezone(timezone.utc)
```

Step by step: parse -> **branch on `dt.tzinfo`** -> attach `Asia/Jerusalem`
**only if the value carries no zone** -> convert to UTC.

**The branch is the whole point, and it is what the stated contradiction missed.**
The code does *not* unconditionally assume Israel. If MAYA sent a zoned value
(`Z` or `+03:00`), `tzinfo` would be non-None, the Israel assumption would be
skipped, and the function would be a **no-op normaliser** — new rows would come
out correct *whatever* the zone. So "new rows are correct" is consistent with
**both** models and, on its own, proves nothing either way.

## 2. How the raw value reaches it — MEASURED

`collect_maya.py:145`: `published = _parse_published(r.get("publishDate"))`, where
`r` is an element of `r.json()` (lines 102/106). **No normalisation, no
pre-processing, no string munging** happens in between. The parser sees the exact
JSON string MAYA sent.

## 3. The four possible models — code behaviour MEASURED offline

Each row assumes an announcement genuinely published **09:10:59 Israel
(= 06:10:59 UTC)**. "OLD" is the pre-fix line
`datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)`.

| Model | raw format | OLD code stored | OLD + backfill(-3h) | NEW code stores |
|---|---|---|---|---|
| **A** naive Israel | `2026-07-16T09:10:59.88` | 09:10:59Z (12:10 IL) ❌ 3h late | **06:10:59Z (09:10 IL) ✅** | **06:10:59Z ✅** |
| **B** aware Israel | `...09:10:59.88+03:00` | 09:10:59Z (12:10 IL) ❌ 3h late | **06:10:59Z (09:10 IL) ✅** | **06:10:59Z ✅** |
| **C** aware UTC | `...06:10:59.88Z` | 06:10:59Z ✅ already right | 03:10:59Z (06:10 IL) ❌ **CORRUPTED** | **06:10:59Z ✅** |
| **D** naive UTC | `2026-07-16T06:10:59.88` | 06:10:59Z ✅ already right | 03:10:59Z (06:10 IL) ❌ **CORRUPTED** | 03:10:59Z ❌ 3h early |

Read the last column: **the NEW code is correct under A, B and C alike.** Only
under **D** does it break. That is the formal statement of §1.

## 4. What MAYA actually sends — MEASURED (the decisive fact)

I did not probe MAYA (per constraints). I did not need to: **21 raw `publishDate`
values captured from live MAYA responses during Phase 2b are still on disk** in
`research/maya_*.json`.

| | |
|---|---|
| captured values | **21** |
| carrying `Z` or an offset | **0** |
| **naive (no zone at all)** | **21** |

Crucially, `research/maya_company_reports_hits.json` captures **the exact endpoint
`collect_maya` uses**:

```
"url": "https://maya.tase.co.il/api/v1/reports/companies"
     publishDate\":\"2026-06-22T23:09:02.677\"
```

Also `research/maya_breaking.json` -> `"publishDate": "2026-07-13T17:29:02.88"`,
plus 19 more in `maya_samples.json`. Distinct values span 2026-05-28 ->
2026-07-13, several companies, two endpoints. **All naive.**

-> **Models B and C are ruled out by measurement.** The raw carries no zone, so
`dt.tzinfo is None` is always true and the Israel branch always fires.

*(Correction to my earlier §B1: I presented "naive" as observed, but had actually
taken it from a code comment. It was INFERRED then. It is MEASURED now — the
comment happened to be right. That was exactly the sloppiness worth catching.)*

## 5. Reconciling the contradiction — observation #2 proves the model

Only **A** and **D** survive §4. They differ solely in what the naive wall clock
*means*: Israel local (A) or UTC (D).

**Observation #2 settles it.** Under **D**, the new code would store every filing
**3h early** (§3, last column). You measured a post-fix filing — website 09:47
Israel, stored 09:47 Israel — as **correct**. Under D it would read 06:47. **So D
is refuted, and the raw is naive *Israel local*: model A.**

That is precisely the Step 2 model. **The fact offered as contradicting it is the
proof of it.** The stated contradiction rested on the premise that the new code
unconditionally assumes Israel; it doesn't (§1), so "new rows are correct" never
conflicted with anything.

**Observation #1 (the 09:10 row) is therefore not corruption — it is a unit
mismatch in the reading.** Under model A: raw `09:10:59` -> OLD code stored
`09:10:59Z` -> backfill -3h -> **`06:10:59+00`**. And `06:10:59 UTC` **is**
`09:10:59 Israel` (IDT, +3) — matching the website exactly. **The row is correct.**
The reported "`published_at = 06:10:59 Israel`" is the *UTC* value `06:10:59+00`
labelled Israel; compared against a website clock in Israel time, a correct row
looks 3h early. The digits `06:10:59` are exactly what model A predicts the stored
UTC to be.

The two observations were, I believe, read with different tools: the **09:47** row
via the UI (which converts to browser-local Israel -> 09:47 -> "correct"), and the
**09:10** row via the DB (which shows UTC -> 06:10:59 -> "3h early"). Both rows are
correct. — **INFERRED** (about how the values were read; I cannot observe that).

The Step 2 evidence still fits A too: a pre-backfill row stored `23:25:00+00` means
raw `23:25:00` = 23:25 Israel (a plausible evening announcement) mislabelled by the
old code; the backfill moved it to `20:25Z` = 23:25 Israel. ✅

## 6. Was sql/003's MAYA half a mistake?

**No — on the evidence available it was correct, and it repaired the rows it
touched.** Plainly: I do not believe ~120 rows were corrupted; I believe they were
fixed, and that the 09:10 row demonstrates the fix working. Assessed on its own,
independent of the SEC half.

**Residual doubt (stated honestly, because acting on an INFERRED claim is what
caused this review):**

- The captures are **3 days to 7 weeks old** (2026-05-28 -> 2026-07-13). If MAYA
  changed `publishDate`'s format since, §4 is stale. Unlikely, not impossible.
- §5's resolution of observation #1 depends on **INFERRING how the value was read**.
  If the 09:10 row's stored value really is `03:10:59+00` (i.e. it reads `06:10:59`
  *after* `AT TIME ZONE 'Asia/Jerusalem'`), then model A is wrong, model C is right,
  and the backfill did corrupt. My own sql/003 verification query prints exactly
  such an `..._in_israel` column, which is a plausible way to have arrived at that
  number — so this doubt is real, not theoretical.
- Everything here concerns the **stored timezone only**. MAYA's own publication
  latency (§B5 above) remains **UNKNOWN**.

## 7. The single cheapest observation that settles it

Print the raw string MAYA sends **right now**, verbatim, from the exact endpoint
the collector uses. Writes nothing to the DB.

```powershell
python -c "import json; from desk.maya_client import harvest_cookies, gate_cleared, make_session, REPORTS_URL; c = harvest_cookies(); print('gate cleared:', gate_cleared(c)); s = make_session(c); r = s.post(REPORTS_URL, data=json.dumps({'pageNumber':1,'companyId':629,'limit':5,'offset':0}), headers={'Content-Type':'application/json','Origin':'https://maya.tase.co.il'}, timeout=30); d = r.json(); rows = d if isinstance(d, list) else d.get('reports') or d.get('data'); print(); [print('id=', x.get('id'), ' RAW publishDate =', repr(x.get('publishDate')), ' ', (x.get('title') or '')[:45]) for x in rows[:5]]"
```

**How to read it** — take the newest `id` and open
`https://maya.tase.co.il/reports/details/<id>`:

| raw string looks like | model | meaning |
|---|---|---|
| `'2026-07-16T09:10:59.88'` and the site shows the **same 09:10** | **A** | Israel local. **Backfill was correct. Nothing to do.** |
| `'2026-07-16T06:10:59.88'` and the site shows **09:10** (3h ahead) | **D** | naive UTC. New code is *also* wrong; backfill corrupted. |
| ends in `Z` / `+00:00` | **C** | UTC. **Backfill corrupted 120 rows** — revert via the rollback. |
| ends in `+03:00` | **B** | aware Israel. Backfill was correct. |

10-second DB cross-check (settles the reading question in §5 — Supabase editor):

```sql
select id, published_at as stored_utc,
       published_at at time zone 'Asia/Jerusalem' as in_israel,
       left(title, 40) as title
from public.filings
where source = 'maya'
order by published_at desc
limit 10;
```

If `in_israel` matches what maya.tase.co.il shows for that announcement, the rows
are correct and model A holds.

---

# MAYA BACKFILL POST-MORTEM (2026-07-16)

My Step 3 falsification condition fired: the 09:10 row reads **06:10:59 after
`AT TIME ZONE 'Asia/Jerusalem'`**, three hours before the MAYA website's 09:10.
I said "if that happens, I'm wrong and the rollback applies." It happened.

**The two earlier sections above are left intact. This is now the THIRD model of
the same field; that record is the point.** MEASURED / DOCUMENTED / INFERRED
marked throughout, and where I cannot establish something I say so.

## Verdict (one sentence, unsoftened)

**Yes — sql/003's MAYA half corrupted the data: the pre-backfill MAYA rows are
now 3 hours EARLY and must be shifted +3h (DST-aware) to be correct. The SEC half
is independently correct and must NOT be touched.**

But the mechanism is NOT the one the task proposes, and I could not fully resolve
it — see §5.

## 1. What the OLD code did — quoted, traced (MEASURED)

Pre-fix `desk/collect_maya.py::_parse_published` (from git `5693bc2`, verbatim):

```python
    try:
        # e.g. "2026-07-13T17:29:02.88" (naive, Israel local) — store as-is UTC-tagged.
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
```

On a naive Israel-local value it **tags UTC without converting**. Traced against
the live 09:10 row (raw `09:10:59` Israel, true instant `06:10:59 UTC`):

| stage | value | in Israel | vs true |
|---|---|---|---|
| TRUE (website 09:10) | `06:10:59+00` | 09:10:59 | 0 |
| **OLD code stored** | `09:10:59+00` | **12:10:59** | **+3h (late)** |
| OLD + backfill ×1 | `06:10:59+00` | 09:10:59 | 0 (correct) |
| OLD + backfill ×2 | `03:10:59+00` | **06:10:59** | **−3h** ← **MEASURED current** |
| NEW code | `06:10:59+00` | 09:10:59 | 0 (correct) |

## 2. Did the OLD code already produce correct UTC? — NO (MEASURED)

**No.** On a naive Israel-local value the OLD code stored the instant **3 hours in
the future** (12:10 Israel for a 09:10 filing). It did not convert; it relabelled.

So the task's premise — *"the collector's parsing was ALREADY correct, so the
pre-existing rows were already right"* — is **false**. The pre-existing rows were
**3h late**, and my Step-2 **code fix was correct and necessary**. The live 09:47
row proves the new code right (below), and a correct new parser is not redundant.

**However — and this is the part that matters — the backfill still corrupted the
data.** "The code fix was needed" and "the backfill was a mistake" are both true
at once. The rows are now 3h EARLY (measured), so the answer to *"was sql/003's
MAYA half a data-corrupting mistake"* is an unqualified **yes**.

Why the new code is trusted (MEASURED): the 09:47 row was collected **after** the
backfill by the NEW code and reads **09:47 Israel = the website** exactly. That
match is only possible if the raw is naive Israel-local (model A) **and** the new
zoneinfo conversion is right. Both hold.

## 3. The "impossible 2am" row — my reasoning was flawed even where the fix wasn't

Step 1 flagged a row stored `2026-07-02 23:25:00+00` (= 02:25 Israel) as
"impossible — MAYA doesn't publish at 2am." Under model A that row's raw was
`23:25` **Israel** — a real 23:25 evening filing — which the OLD code mis-stored
as 23:25 UTC (02:25 Israel display). So:

- The row was **not correct "all along"** — it was genuinely 3h late (a 23:25
  filing shown at 02:25). MEASURED via the OLD-code trace.
- But my **argument** was wrong: my Step-3 probe shows MAYA **does** publish late
  at night (Form 4s at 23:09, 23:27 — MEASURED). So "2am is implausible" was never
  valid evidence; I was reasoning about a corrupted display and reached the right
  −3h direction by luck, not rigour. That is the same failure mode as calling
  "naive" MEASURED in Step 1 when it was only INFERRED.

## 4. SEC half — UNAFFECTED, do NOT revert (assessed separately)

The SEC and MAYA fixes are **independent UPDATEs on disjoint row sets**
(`source='sec'` vs `source='maya'`). The SEC half is independently verified:
BAC 8-K `2026-07-14 14:45:08+00` = **10:45 New York = market open** (MEASURED).
**Keep it. Any MAYA repair must filter `source='maya'` and never touch `sec`.**

## 5. The mechanism — what I can and CANNOT establish (honest unknown)

**What's certain (MEASURED):** current MAYA pre-backfill rows sit at **true − 3h**.
The fix depends only on this, and is well-defined: **+3h, DST-aware** (§6).

**What I cannot establish:** *how* they got there. The trace (§1) shows current =
OLD − 6h, i.e. the −3h shift landed **twice**. Two mechanisms produce that, and
I cannot distinguish them without the DB:

- **(a) the backfill ran twice** (net −6h on OLD-3h-late rows). The
  `applied_migrations` guard in sql/003 should have blocked a second run, so this
  implies the guard was bypassed, an unguarded UPDATE was run, or an early
  version executed — I have no evidence either way.
- **(b) the backfill ran once on rows the new code had already corrected**
  (correct − 3h). Requires the corrupted rows to have been NEW-code rows at
  backfill time.

**And a genuine anomaly I could not resolve:** the 09:10 filing published **09:10
Israel = 06:10 UTC today**, which is *after* the backfill's stated run time
(**06:36 Israel = 03:36 UTC**). A one-shot backfill at 03:36 UTC **cannot** have
touched a row that did not exist until 06:10 UTC — yet the row is corrupted. That
points to the shift being applied **repeatedly / after 09:10 today**, or to a
collection-time I don't have. I will not invent a story to close this gap.
**This is exactly the kind of thing that must be checked against the live DB
before any repair runs — it is the whole lesson of this episode.**

To settle it before Step 5, run (read-only, Supabase editor):

```sql
select f.id, f.source, f.published_at, f.fetched_at,
       f.published_at at time zone 'Asia/Jerusalem' as filed_il,
       (select applied_at from public.applied_migrations
        where name = '003_fix_filing_timestamps') as backfill_ran_at
from public.filings f
where f.source = 'maya'
order by f.fetched_at desc
limit 20;
```

`fetched_at` is set at INSERT and is **not** touched by the backfill, so
`fetched_at` vs `backfill_ran_at` tells you, per row, whether it predates the
backfill (corrupt → needs +3h) or postdates it (correct → leave). If any row has
`fetched_at` after `backfill_ran_at` **and** `filed_il` 3h early, mechanism (a)/(b)
are both wrong and the shift is ongoing — **stop and diagnose further, do not
bulk-shift.**

## 6. What a MAYA-only repair must do (spec for Step 5 — NOT written here)

- **Rows:** `source='maya'` **AND** `fetched_at < (003's applied_at)`. Scope by
  **fetched_at, not published_at** — published_at is the corrupted field, so it
  cannot select its own corrupted population reliably, and future NEW-correct rows
  with early publish times would be misclassified. This also structurally
  **excludes the correct post-backfill row(s)** (the 09:47 row and any collected
  since): touching them is *avoidable*, and this predicate is the guard that
  avoids it. Applying +3h to the correct 09:47 row would push it to **12:47
  Israel** — re-breaking it (MEASURED). Verify the population with §5 first.
- **Direction / magnitude:** **forward, +3h in summer / +2h in winter**, via the
  DST-aware inverse — the same formula as sql/003's *rollback*, but MAYA-only:
  `set published_at = (published_at at time zone 'Asia/Jerusalem') at time zone 'UTC'`.
  Verified: it takes the measured `03:10:59+00` → `06:10:59+00` = true, in **one**
  application (MEASURED). Do **not** reuse `003_..._rollback.sql` as-is — it also
  reverses the correct SEC half.
- **⚠️ If §5 shows net −6h (double application), +3h once is still correct** — the
  fix is defined by *current state → true*, which is −3h regardless of how it got
  there. But confirm current state per §5 before running.

## 7. What applied_migrations must record

- Add a **new** marker for the repair, e.g. `004_fix_maya_backfill`, written in
  the **same transaction** as the UPDATE, with the DO-block guard refusing to act
  if it's already present — so this repair cannot itself double-apply (the exact
  failure that (a) would represent).
- **Leave the `003_fix_filing_timestamps` marker in place.** The SEC half of 003
  is valid and must stay recorded; removing it would invite a re-run of 003 that
  re-breaks SEC. The repair is its own migration, not a rollback of 003.
- **Caveat, stated plainly:** if mechanism (a) is real, the `003` marker was
  present and a second shift happened *anyway*. So the 004 guard must not be
  trusted blindly — pair it with the fetched_at scope and the §5 pre-check, not
  the marker alone.

## 8. The lesson (third model, same field)

A blanket timestamp shift over an assumed population, run without first verifying
each row's state against ground truth, is how a correct code fix and a plausible
data fix combined to corrupt 160 rows. The code fix was right. The backfill was
run on an assumption I never checked against the live rows — and I marked the
supporting evidence "MEASURED" when it was INFERRED. **Step 5 verifies against the
MAYA website / fetched_at BEFORE shifting anything.**
