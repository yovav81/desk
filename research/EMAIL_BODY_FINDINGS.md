# Email body + attachments in DESK — Phase 8 step 4 investigation

**Date:** 2026-07-17 · **Scope:** investigation only, no code. MEASURED /
INFERRED marked throughout. No DB/IMAP access; Supabase quotas from the live
pricing page (MEASURED 2026-07-17).

## Headline: the body is ALREADY stored — attachments are the real build

`emails.body_text` holds the full (flattened) body of every email since day
one; the UI just never displays it. Attachments, by contrast, are **explicitly
discarded** at parse time. So part C is mostly a UI-rendering task over
existing data, and part B is the genuine schema+storage+collector project.

## A. What we store today

### A1. What collect_email extracts (MEASURED, from the code)
Per IMAP message: `From`, `Subject`, `Message-ID`, `Date`, and a body via
`extract_body_text()`:
- multipart walk **skips any part with `Content-Disposition: attachment`**
  (`collect_email.py`: `if part.get_content_disposition() == "attachment":
  continue`) — **attachments are dropped on the floor, never stored**;
- takes the FIRST `text/plain` part, else the FIRST `text/html` part
  **flattened to plain text** (`BeautifulSoup.get_text(" ", strip=True)`) —
  the original HTML markup is discarded;
- **nothing is truncated** — the full text goes into the row.

### A2. The emails table (MEASURED, db.py — quoted)
```python
emails = Table(
    "emails", metadata,
    Column("id", Integer, primary_key=True),
    Column("sec_id", String(32), ForeignKey("securities.sec_id"), nullable=True),
    Column("sender", String(255), nullable=False),
    Column("subject", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=True),
    Column("body_text", Text, nullable=True),
    Column("matched_by", String(16), nullable=True),
    Column("message_id", String(998), nullable=False, unique=True),
)
```
**Body: provided for and populated** (`body_text`, unbounded `Text`).
**Attachments: no provision anywhere.** Also MEASURED: the UI's list query
(`useNews.js`) selects `id, sec_id, sender, subject, received_at` — body_text
is already excluded from the feed fetch, so lazy-fetch-on-click needs no
change to the list path.

### A3. Adding what's missing + history
- **Body:** nothing to add — render what exists.
- **Attachments:** a new `email_attachments` table (email_id FK, filename,
  mime, size, object_path) + a Storage bucket + collector upload logic + a new
  backend secret (see B3).
- **History backfill — feasible and cheap (honest call: do it, one-shot).**
  The collector never deletes or moves mail (MEASURED docstring) and stores
  `message_id` for every row, so a one-shot CLI can IMAP-search ALL messages,
  match `Message-ID` → existing row, and upload just the attachments. Inbox
  volume to date is small (days old), so this is minutes of runtime. If it
  ever proves fiddly, the fallback is honest too: history stays
  subject+body-only and attachments start from deploy day.

## B. Attachments — the core question

### B1. How IMAP exposes them (MEASURED mechanics, INFERRED sizes)
MIME multipart parts with `Content-Disposition: attachment` (plus inline PDFs
that some senders mark `inline` — the collector should key on content type
`application/pdf`/Office MIME types as well as disposition). Per part:
`part.get_filename()` (needs RFC 2231 decoding — `decode_mime_header` already
exists and can be reused) and `part.get_payload(decode=True)` bytes.
**Sizes: analyst notes typically 0.5–5 MB PDF — INFERRED, not measured;**
assume ~1.5 MB average for the math below.

### B2. Storage options + the free-tier math

Volume assumption: ~40 attachments/day × ~1.5 MB ≈ **60 MB/day ≈ 1.8 GB/month**
(INFERRED average on the stated 30–50 emails/day).

| Option | Math | Verdict |
|---|---|---|
| **(a) Supabase Storage bucket** | Free plan (MEASURED from pricing): **1 GB storage, 5 GB egress/mo, 50 MB max upload**. 1 GB ÷ 60 MB/day ≈ **fills in ~17 days** → retention is MANDATORY: **~14 days ≈ 0.85 GB** fits with headroom. Egress fine (opening ~10 PDFs/day ≈ 0.5 GB/mo ≪ 5 GB). Pro ($25/mo): **100 GB ≈ 4.5 YEARS** at this rate. | ✅ **Recommended.** DB row stores the object path only. |
| (b) BYTEA in Postgres | The whole DB budget is **500 MB** and already holds quotes/news/history — 60 MB/day exhausts it in **under a week**. | ❌ Disqualified by arithmetic. |
| (c) Metadata only | Stores filename+size, loses the file — and the attachment IS the payload (the analyst note), per the locked requirement. | ❌ Defeats the purpose (acceptable only as the fallback for over-cap files: store metadata, skip the bytes, log). |

**Retention recommendation:** prune Storage objects (and their metadata rows)
older than **14 days on the free tier**; the email row itself (subject, body,
attribution) is tiny and stays forever. If two weeks is too short for how
you use analyst notes, the alternative is Pro — that's the decision (§D).

### B3. Access control — don't make the bucket the RLS hole
- **Private bucket + signed URLs. Never public:** these are third-party
  analyst reports (copyright + client confidentiality); a public bucket means
  anyone with a URL reads them forever.
- Frontend (anon key + logged-in user): `supabase.storage.createSignedUrl()`
  works when a **Storage RLS policy on `storage.objects` grants SELECT to
  `authenticated`** for this bucket — consistent with the shared-pool model
  (any logged-in employee reads any attachment), and nothing is reachable
  logged-out. Short expiry (~60 min) per click.
- **Collector upload needs a NEW backend secret:** the collectors speak raw
  Postgres (`DESK_DB_URL`), but Storage is an API — uploading requires the
  **service_role key** (or Storage S3 keys) as a GitHub Actions secret,
  backend-only, same discipline as the existing secrets. It must NEVER appear
  in web/. Flagged now so it's a deliberate decision, not a surprise.

### B4. Viewing per file type (v1 recommendation)
- **PDF → open the signed URL in a new tab.** Browser-native viewer, zero
  code, works on desktop and mobile. Embedded viewer = later polish, not v1.
- **Office docs (docx/xlsx/pptx) → download-only** via the same signed URL —
  browsers can't render them natively and embedding Office viewers means
  third-party services. Honest v1.

## C. Body + rendering (now the easy half)

1. **What exists is flattened plain text** (HTML already stripped at collect
   time — MEASURED). Bodies are small (INFERRED <50 KB typical); the column is
   unbounded and PG-cheap next to PDFs. **No ingest truncation needed**; the
   UI can clamp the initial render (~20 KB + "הצג עוד") purely for layout.
2. **Rendering safety: plain text only, v1.** Since no HTML is stored, there
   is nothing to sanitize — render `body_text` in a `whiteSpace: 'pre-wrap'`,
   `dir="auto"` block and the XSS question doesn't exist. **No DOMPurify (the
   no-new-deps habit holds), no iframe.** If rich HTML email is ever wanted,
   that's a collector change (store HTML) + a sandboxed `iframe srcdoc`
   discussion — explicitly out of v1.
3. **UI pattern: expand-in-place**, one pattern for desktop and the Phase 7
   mobile tab — clicking an email row toggles it open inside the feed
   (no drawer/modal/navigation state; works at any panel width).
   **Lazy-fetch confirmed:** the list query already omits `body_text`; on
   expand, fetch `body_text` by id + the attachment rows, then mint signed
   URLs only when a file is clicked.

## D. Plan

| # | Step | Contents | Yours |
|---|---|---|---|
| 1 | Schema + storage | `email_attachments` table (sql/004 + db.py), private bucket, Storage RLS policy for `authenticated` | Create bucket, run SQL, add the service-key secret |
| 2 | Collector | Extract attachment parts (disposition OR pdf/office MIME), upload to Storage, insert metadata; **size cap ~20 MB** (over-cap → metadata-only + log, and the free plan hard-caps uploads at 50 MB); fail-soft (upload failure never loses the email row); retention prune each run (like price_history) | — |
| 3 | History backfill | One-shot CLI keyed on `message_id` (A3) | Run it once |
| 4 | UI | Expand-in-place + plain-text body + attachment buttons → signed URLs | Eyeball on desktop + phone |

**Risk check:** the attribution ladder is untouched (additive columns/tables);
feed performance untouched (list query unchanged — body stays lazy);
`emails` read RLS already exists. The one new attack surface is the bucket —
closed by private+signed-URL (B3). **Actions runtime:** +~2–6 s per run with
attachments present (1–3 emails × 1.5 MB upload per 15-min cycle, INFERRED) —
irrelevant on a public repo's free minutes. The retention prune is one DELETE
+ a Storage list/remove per run — seconds.

**Decision points for you:** (1) **retention: ~14 days free vs Pro $25/mo
(~years)** — this shapes step 1; (2) approve the new backend secret
(service_role key in Actions); (3) the size cap (default 20 MB unless you
expect bigger decks).
