# Email→security attribution — Phase 8 step 1 investigation

**Date:** 2026-07-17 · **Scope:** investigation only, no code. MEASURED /
INFERRED marked throughout. No DB/IMAP access — code + schema only.

## A. The bug: why every email is "CITIGROUP INC"

### A1. The exact code (MEASURED — `desk/collect_email.py:74-93`)
```python
def tag_security(sender: str, subject: str, body: str, secs: list[dict]):
    """Best-effort match against sender / subject / body. ..."""
    sender_l = sender.lower()
    ...
    for sec in secs:
        needle = sec["symbol"].split(".")[0].lower()
        if needle and needle in sender_l:
            return sec["sec_id"], "sender"
```
`sec_id` is set once at collect time (`collect()` line 140:
`sec_id, matched_by = tag_security(...)`) and stored forever.

### A2. The mechanism, precisely (MEASURED from code)
**Citigroup's symbol is `C`. The sender tier does a lowercase SUBSTRING check,
and every email sender contains the letter "c" — because every sender address
ends in `.com`** (e.g. `yovav81@gmail.com`). So `needle="c"` → `"c" in
sender_l` → **True for essentially every email on Earth**, returning
`('C', 'sender')` before any other logic runs. The sender tier is FIRST, so it
short-circuits the subject/name tiers entirely.

It is not a hardcoded default, not an alphabetical accident, not a join —
it's a **single-letter ticker meeting a substring match**, deterministic for
any `@gmail.com`/`.com` sender regardless of securities iteration order (C is
the only current symbol that substring-matches a typical sender). The bug was
born the day `C` was onboarded (Phase 3 CIK list: AAPL/BAC/C/MSFT/SAP) — the
same single-letter-ticker class of problem the search box hit ("C" must rank
exact-first) — and `matched_by='sender'` on the mis-tagged rows is its
fingerprint.

**Confirmation query (optional — mechanism is already pinned from code):**
```sql
select sec_id, matched_by, count(*) from public.emails group by 1, 2;
-- expect: ('C', 'sender', ~8)
```

### A3. What attribution logic exists today (MEASURED)
Three substring tiers, in order: (1) bare symbol in **sender**; (2) full
lowered `name` OR symbol substring in **subject**; (3) full lowered `name`
substring in **body**. All substring, no word boundaries, no length guard, no
multi-match handling (first hit wins), no Hebrew awareness. Even without the C
bug, tier 2/3 recall is near zero for Hebrew mail (see B1) and tier 2's
symbol-substring has the same class of false positives (`SAP` inside a word).

## B. What is available to match against

### B1. Matching material per security row (MEASURED)
| Field | Content | Appears in a Hebrew forwarded email? |
|---|---|---|
| `name` | **Two populations:** seeded rows are ENGLISH ("Teva Pharmaceutical Industries", "CITIGROUP INC"); UI-added TASE rows are the FULL Hebrew registered name (`נקסט ויז'ן מערכות מיוצבות בע"מ`) | English names: ~never in Hebrew mail. Full registered names: rarely verbatim — emails say the brand (`נקסט ויז'ן`), not the registered form |
| `symbol` | US/GLOBAL letter ticker; TASE = the numeric string (UI-added) or letter code (seeded) | English tickers (TEVA, LUMI) appear in SOME emails/links |
| `sec_id` | TASE security number / US ticker | The 6-7 digit number appears in some broker/filing-style emails |
| `yahoo_symbol` | e.g. `LUMI.TA` | The bare prefix sometimes; the `.TA` form rarely |

### B2. Hebrew matching without an LLM — honest assessment
The email says `נקסט ויז'ן (ת.יתר)- עדכון`; the DB says
`נקסט ויז'ן מערכות מיוצבות בע"מ`. Full-name exact/substring match fails —
matching must be **token-based**:

- **Distinctive vs. noise tokens:** strip legal/generic tokens
  (בע"מ, מערכות, מיוצבות?, החזקות, אחזקות, קבוצה, תעשיות, בנק, ישראל, נדל"ן,
  אנרגיה, גרופ…) and most brands reduce to 1-2 distinctive tokens: נקסט+ויז'ן,
  שפיר, רימון, בזן, לאומי. Note אנרגיה MUST be noise (משק אנרגיה vs תומר
  אנרגיה both contain it); בנק must be noise (לאומי vs a future הפועלים).
- **Normalization needed:** gershayim/quote variants (`בע"מ` vs `בע״מ`),
  punctuation, the ־ maqaf; prefix-letters (ו/ה/ל attached) reduce recall a
  bit if ignored — acceptable.
- **English symbol as a whole word** (≥2 chars, case-respecting) is high
  precision when present.
- **The numeric security number** as a whole word is near-perfect precision
  when present.

**Achievable without an LLM (honest):** *precision* can be engineered to
~100% by construction — every ambiguous or weak signal resolves to NULL
(macro), so wrong attribution ≈ never. *Recall* is genuinely UNKNOWN until
measured in production; on brand-style subjects like the 8 test emails
(נקסט ויז'ן, רימון, בזן, שפיר, מנועי בית שמש — distinctive brands), token
matching should attribute **most** of them, **provided those securities exist
in `securities` at all** — attribution can only point at rows we have; an
email about an unwatched company correctly lands in macro. I will not quote a
fake percentage; the design includes the logging to measure the real rate
(§D).

### B3. Prior art in this repo (MEASURED)
None reusable. All "normalize" hits are currency (`normalize_currency`);
`securities.py find()` is the same naive substring class as the bug;
`onboarding.py` has Hebrew *detection* (`HEBREW_RE`) but no tokenization.
The tokenizer/stopword list will be new code — small, self-contained, and it
belongs in the collector (house rule: no matching logic in the UI).

## C. Where should attribution run?

**Collect time — the house pattern — with a NULL-only re-attribution sweep.**

- **Collect-time** (recommended): enrichment-by-collector is how everything
  else works (enrich/maya_ids/sec_ids); the stored `sec_id` survives UI
  rewrites; matching runs once per email, not on every page load; the matching
  code lives next to the other Python text handling, not duplicated in JS.
- **Read-time** would adapt automatically when the watchlist grows, but costs
  a re-match of every email on every load, in a second language (JS), against
  the design rule that collectors enrich and the UI reads.
- **The "security added next week" case:** an email that arrived before its
  company existed in `securities` sits at NULL (macro). If the company is
  added later, should the old email re-attribute? Product answer: nice but not
  critical (emails are timely). Cost of having it anyway: **near zero** — a
  sweep over `emails WHERE sec_id IS NULL` re-running the same matcher,
  bounded (recent N days), idempotent, only ever *filling* NULLs (never
  rewriting a non-NULL). Fold it into the collector run, collect_enrich-style.
- **The 8 mis-tagged rows:** they are *wrongly non-NULL*, so the NULL-sweep
  won't touch them. They are precisely identifiable — `matched_by='sender'`
  is the bug's fingerprint (the entire sender tier is being removed). One-shot
  fix, run by you:
  ```sql
  update public.emails set sec_id = null, matched_by = null
  where matched_by = 'sender';
  ```
  …after which the NULL-sweep re-attributes them with the new ladder.

## D. The recommended matching ladder (design only)

Confidence-ordered; first tier that produces EXACTLY ONE security wins;
**multi-match at any tier → NULL + logged as ambiguous** (ambiguous is macro —
wrong attribution is worse than none). Subject outranks body within each tier.

1. **Numeric security number** (`\b\d{6,9}\b` ∩ known sec_ids) in
   subject, then body. Near-perfect precision.
2. **English ticker as a whole word, len ≥ 2** (symbol and yahoo_symbol
   prefix; case-respecting match against the ORIGINAL text, not lowered).
   **Single-letter symbols are excluded from text matching entirely — the C
   lesson, structural, not a special case.**
3. **Distinctive Hebrew tokens** from `securities.name`: normalize
   (gershayim/quotes/punctuation), drop stopword tokens, then require the
   remaining distinctive phrase/tokens as whole words — subject first, body
   second. A single generic token can never match (it's in the stopword
   list); a token shared by two securities → both match → NULL by the
   multi-match rule.
4. **No match → `sec_id = NULL`** — the legitimate, already-working macro
   home. Never a guess.
- **Sender tier: deleted.** Sender text (forwarding user's address) carries
  no security signal; it was only ever the bug.
- **Never overwrite:** the matcher only sets `sec_id` on insert or on
  NULL-sweep rows; a non-NULL `sec_id` is never recomputed.
- **Logging (the collect_enrich pattern):** per email — truncated subject +
  tier hit (`number`/`symbol`/`tokens`) or NULL reason (`none`/`ambiguous:
  [candidates]`); per run — `attributed=N/M (number=a symbol=b tokens=c)
  ambiguous=d none=e`, so the real production attribution rate is measured,
  not estimated.
- `matched_by` values change meaning (new: `number|symbol|tokens`) — the
  column is already free-text VARCHAR(16), no schema change (MEASURED).
