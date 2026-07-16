# Frontend freshness + labels — Phase 5 step 1 investigation

**Date:** 2026-07-16 · **Scope:** investigation only, no code written.
Three issues in `web/` (live at desk-henna.vercel.app). MEASURED / DOCUMENTED /
INFERRED marked throughout.

**Do these share a root cause?** No. Issue 3 is a label bug. Issues 1 and 2 are
both about *freshness* and belong to one theme, but need separate fixes. Details
in §D.

---

## A. ISSUE 3 — tag shows the security number, not the name

### A1. Where (MEASURED — exact location)
The tag text is decided in **`web/src/App.jsx:186`**, not in the feed component:

```js
const secLabels = Object.fromEntries(wl.rows.map((r) => [r.sec_id, r.symbol || r.sec_id]));
```

Flow: `App.jsx` builds `secLabels` → passes to `News.jsx:18` → `News.jsx:97`
does `secLabel={secLabels[item.sec_id]}` → `FeedItem.jsx:42-54` renders it.
`FeedItem` only ever receives the finished string; it has no bug.

### A2. Why the name isn't used (MEASURED)
The map uses **`symbol`**, falling back to `sec_id`. For a TASE security, `symbol`
holds the number (or is empty → falls back to `sec_id` = the number). So the tag
shows `604611`. It never consults `name`.

`name` **is available** — no data problem: `useWatchlist.js:89` already fetches
`sec_id, symbol, name, asset_type, market, price_source`, so every `wl.rows`
entry carries `name`. The watchlist panel shows the Hebrew name precisely because
`format.js:53 displayName()` uses `name` for TASE (`symbol` for US). The tag map
just doesn't reuse that rule. **This confirms the task's hypothesis: UI-layer
bug, not a data issue.**

### A3. Minimal fix (INFERRED — a design decision is embedded, see A4)
**Component change only, no query change** (`name` is already fetched). Build
`secLabels` from a name-aware rule instead of `symbol || sec_id` — the same
US→symbol / TASE→name logic `displayName()` already encodes. One line in
`App.jsx`. The stale comment on `App.jsx:184-185` ("short label (symbol)") should
change with it.

### A4. Is this the same as GOLD's "Hebrew names for tags" item? (INFERRED)
**Overlapping, but not identical — and the difference is the one open decision.**
- The *bug* (number instead of any name) is fixed by A3 using `securities.name`.
- But `name` is the **full registered name** — `securities.name` for 604611 is
  `בנק לאומי לישראל בע"מ`, not the short `לאומי` the task wants on a compact tag.
  **There is no short-name column** (checked `desk/db.py`: `securities` has
  `sec_id, symbol, name, …, cik`; `tase_securities.name` is also the full
  `longName`). So showing exactly `לאומי` needs either a new short-name field, a
  truncation heuristic, or accepting the full name in the tag.
- **So:** "show a name instead of the number" = the immediate small fix.
  "Show a *short* Hebrew name" = the separate low-priority GOLD item, which needs
  a data source that doesn't exist yet. They are adjacent, not the same.

---

## B. ISSUE 1 — page never auto-refreshes

### B1. How web/ fetches today (MEASURED)
**Fetch-on-mount only. No polling, no subscription, no focus refetch anywhere.**
- `useWatchlist.js:53` — `useEffect`, deps `[authUser?.id, authUser?.email]`.
- `useNews.js:52` (dashboard feed) — `useEffect`, deps `[]` (once).
- `useNews.js:99` (`useSecurityFeed`) — deps `[secId]`.
- `usePriceHistory.js:18` — deps `[secId]`.

The only subscription in the app is `supabase.auth.onAuthStateChange`
(`App.jsx:22`) — **auth state, not data.** `grep` for
`channel|realtime|subscribe|postgres_changes|setInterval|visibilitychange` across
`src/` finds nothing data-related. So after mount, data is frozen until a full
reload re-runs the effects — which is exactly the F5-only behaviour measured for
the 09:47 filing.

### B2. Options (cost / free-tier load / failure modes)

| Option | Impl cost | Free-tier load | Failure modes |
|---|---|---|---|
| **Interval poll** (`setInterval` → refetch) | Low — wrap existing fetches | Every tick = the feed's 3 queries + watchlist. At 30 s that's ~99% wasted (writes come every 74–128 min). At 2–5 min: negligible for a few users. | None serious; wasted requests; possible flicker if refetch clears state mid-render. |
| **Supabase Realtime** (subscribe to `filings`/`news` INSERT) | Medium — channel setup, reconnection, dedupe into existing state | Near-zero when idle (one WebSocket/client, event-driven) | **Silently does nothing if Realtime isn't enabled on the tables** (see B3); WebSocket drops/reconnects; RLS applies to Realtime too, so the anon read policy must exist on those tables. |
| **Refetch on focus / visibilitychange** | Low — one listener → refetch | Only when the user returns to the tab — very low, well-matched to a glanced-at dashboard | Won't update a tab left focused-and-idle (mitigated by pairing with a slow interval). |

### B3. Is Realtime enabled on this project's tables? **UNKNOWN (cannot tell from the repo)**
Whether the `supabase_realtime` publication includes `filings`/`news` is a
**server-side setting in the Supabase dashboard**, not visible in the codebase.
The client uses a default `createClient` with no channels. I will not guess —
this must be checked in the dashboard (Database → Replication / Publications)
before Realtime is considered viable.

### B4. Recommendation (INFERRED)
**Refetch on `visibilitychange`/focus, paired with a slow interval (~2–5 min) as
a backstop.** Reasoning: writes land every 74–128 min (MEASURED, from
FRESHNESS_FINDINGS), so *instant* push buys almost nothing over "refresh when the
user looks." Focus-refetch matches real dashboard use (tab back in to check),
costs ~nothing, and needs **no server config**. Realtime is the "correct"
real-time answer but is disproportionate here and is **blocked on B3** (verify +
possibly enable Realtime, and confirm the anon read policies). Avoid a fast
interval — 30 s polling would be ~99% wasted requests.

---

## C. ISSUE 2 — "last updated" indicator

### C1. What each candidate would mean, and whether the data exists

| What it shows | Data available? | Honest? |
|---|---|---|
| **(1) When the browser last fetched** | **Yes, trivially** — record a client timestamp when a fetch resolves. No backend. | **Misleading.** After mount it never changes (Issue 1), so it would read "updated just now / 0 min ago" *forever* while the data silently ages. Given Issue 1, this actively lies. |
| **(2) Newest item we hold** (`max(published_at)` across the feed) | **Yes, now** — `published_at` is already fetched; no schema change. | Honest about *content* recency ("newest item: 09:47"). But does **not** reveal a stalled pipeline: if MAYA published nothing for 3 h, this reads 3 h old even if collectors ran 1 min ago. |
| **(3) When collectors last ran** (true pipeline freshness) | **No — not reliably. Needs new schema.** | The genuinely useful signal, but unavailable today. |

### C2. Why (3) needs new schema (MEASURED from `desk/db.py`)
- **No run-log / heartbeat table exists** (checked `db.py` — none).
- `quotes` (upserted every price run) has **`as_of`** = the *price date*, and
  `anchors_date` = a calendar day; **no run timestamp**. So even the one table
  written on every run can't tell you *when* the run happened.
- `news.fetched_at` / `filings.fetched_at` exist, but a run that inserts **no**
  new rows doesn't advance them — so they can't prove "the pipeline is alive,"
  only "the last time something new was inserted."
- `emails` has only `received_at`.

  ⚠️ **fetched_at conflict — flagging, not resolving (we were burned by exactly
  this).** The task states as MEASURED that `filings.fetched_at` is *overwritten
  every run* (all rows identical to the microsecond), so it's a run-time, not a
  per-row arrival time. But the code writes `filings` via `insert_ignore`
  (`ON CONFLICT DO NOTHING`) with `fetched_at server_default now()` — which
  should **not** overwrite existing rows, making it a per-row first-seen time
  (my reading — **INFERRED**). These conflict. I cannot reconcile them from code
  alone, and **whichever is true, it doesn't rescue option (3):** neither a
  run-time-overwrite nor a per-row-insert advances on a no-op run. **Do not build
  anything on `fetched_at`'s semantics until it's confirmed against the live DB.**

  Minimum for a real option (3): a tiny **`collector_runs`** table (or one row
  per source) that **every** collector updates with its finish time on **every**
  run, no-op or not. That's a backend change touching each collector — described,
  not designed, per scope.

### C3. Recommendation (INFERRED)
- **Do not show (1).** It's the easy one and it's the misleading one — combined
  with Issue 1 it would read "just now" permanently.
- **Ship (2) now** — "newest item: HH:MM" from `max(published_at)`, zero backend,
  honest about content age, and it naturally rides along with the Issue-1
  refetch work.
- **Treat (3) as its own backend project.** It is the true "is the pipeline
  alive" indicator the 74–128 min gaps make valuable, but it needs the new
  `collector_runs` schema + every collector writing to it. Bigger than a UI task.

---

## D. Synthesis — one change or three?

**Three separate changes.** Issue 3 is a label bug (unrelated). Issues 1 and 2
share the "freshness" theme and could ship as one pass, but are technically
distinct (a refetch mechanism vs. a freshness readout).

### Order by value-per-effort

| # | Issue | Size | Why |
|---|---|---|---|
| 1 | **Issue 3 — tag name** | **Small** | One line in `App.jsx` reusing an existing rule; `name` already fetched. Clear bug, immediate win. |
| 2 | **Issue 1 — auto-refresh** | **Small–Medium** | Focus + slow interval, no backend, no server config. Kills the manual-F5 problem. (Realtime path is Medium and blocked on B3.) |
| 3 | **Issue 2 — last-updated** | **Small if (2), Big if (3)** | The honest *cheap* version (newest-item) is small and can ride with Issue 1. The *true* freshness version (collectors-last-ran) is the big one — new schema + every collector. |

### Bigger than they look
- **Issue 2 option (3)** — looks like "add a label," is actually "add a run-log
  table and wire every collector to it."
- **Issue 1 via Realtime** — looks like "subscribe," but hinges on an
  **unverified** server setting (B3) and on RLS read policies existing for
  `filings`/`news`. The focus+interval route sidesteps both.
- **Issue 3's "short Hebrew name"** — the *number→name* fix is small; the
  *full-name→`לאומי`* refinement needs a short-name source that doesn't exist.
