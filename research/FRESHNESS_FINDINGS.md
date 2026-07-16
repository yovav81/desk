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
