"""Collect emails from a dedicated Gmail inbox via IMAP into the DB.

Raw data only: stores body text plus a security attribution from a strict
confidence ladder (security number > whole-word ticker > distinctive name
tokens; ambiguous/none -> sec_id NULL = macro — skip, don't fabricate). A
NULL-only sweep each run re-attributes old emails when securities are added
later; a stored non-NULL sec_id is never rewritten. No summarization. Never
deletes or moves mail. Messages are marked \\Seen only
after they have been successfully parsed and inserted (or found to already
exist), so a failure mid-run leaves a message UNSEEN for retry next time.

Required env: GMAIL_USER, GMAIL_APP_PASSWORD. If either is missing, this
exits cleanly (no exception) with a log message — meant to be a safe no-op
in local/dev environments without mail credentials configured.
"""
import email
import hashlib
import imaplib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from sqlalchemy import bindparam, select, text, update

from desk.db import emails, get_engine, init_db, insert_ignore, securities

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_email")

IMAP_HOST = "imap.gmail.com"

# --------------------------------------------------------------------------- #
# Attachments (Phase 8 step 5). Files go to a PRIVATE Supabase Storage bucket  #
# (signed-URL access only); this table row is just metadata. The bucket is     #
# created by hand in the dashboard — see sql/004_email_attachments.sql.        #
# --------------------------------------------------------------------------- #
BUCKET = "email-attachments"
# Locked cap: bigger files keep a metadata row (storage_path NULL) so the UI
# can show "attachment exists, too big to store" — but the bytes are skipped.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
# Retention: the free tier gives 1 GB of Storage and ~60 MB/day of analyst PDFs
# fills that in ~17 days (research/EMAIL_BODY_FINDINGS.md). 14 days keeps
# ~0.85 GB with headroom. The sweep deletes OBJECTS + METADATA ROWS only —
# the emails row and body_text are NEVER touched.
RETENTION_DAYS = 14
# Attachment-worthy parts: anything explicitly marked attachment, plus
# pdf/office MIME types some senders ship as inline.
DOC_MIME_PREFIXES = (
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats",
    "application/vnd.ms-",
)

# email_attachments is not yet declared in desk/db.py (created by sql/004 on
# the live DB), so raw SQL until db.py catches up — the sec_ids precedent.
# ON CONFLICT (email_id, filename) DO NOTHING = the idempotency key: a re-run
# can never duplicate a metadata row, and uploads use x-upsert (same path
# overwritten in place), so re-processing a message is always safe.
_INSERT_ATTACHMENT = text(
    "insert into email_attachments (email_id, filename, size_bytes, content_type, storage_path)"
    " values (:email_id, :filename, :size_bytes, :content_type, :storage_path)"
    " on conflict (email_id, filename) do nothing"
)
_EMAIL_ID_BY_MSGID = text("select id from emails where message_id = :message_id")


def _storage_config() -> tuple[str, str]:
    """Storage endpoint + service_role key, env-only. The key BYPASSES RLS —
    it lives in GitHub Actions Secrets and nowhere else, and is never logged
    or echoed (errors name the VARIABLE, never the value)."""
    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise SystemExit(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — required to store "
            "email attachments. Both are GitHub Actions Secrets (the key is the "
            "service_role secret: backend-only, never in web/, never in logs)."
        )
    return url, key


def sanitize_filename(name: str) -> str:
    """Storage-safe filename: path components stripped (kills '../evil'),
    quotes removed, anything outside letters/digits/Hebrew/space/._- becomes
    '_', no '..' runs, capped length. Never returns empty."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = _QUOTES_RE.sub("", name)
    name = re.sub(r"[^0-9A-Za-z֐-׿ ._-]+", "_", name)
    name = re.sub(r"\.{2,}", ".", name).strip(" .")
    return name[:150] or "attachment"


_EXT_RE = re.compile(r"\.([A-Za-z0-9]{1,5})$")


def storage_key(email_id: int, filename: str) -> str:
    """ASCII-only, collision-safe OBJECT KEY. Supabase Storage rejects
    non-ASCII keys with HTTP 400 "The object name contains invalid characters"
    (hit in production 2026-07-17 with a Hebrew PDF name; documented in
    supabase/storage#133 — their own UI sanitizes to ASCII for this reason).
    The key is DERIVED, not transliterated: {email_id}/{sha1(name)[:16]}{.ext}
    with the extension whitelisted and lowercased. The HUMAN name — Hebrew
    intact — lives in the metadata row's `filename` column; the UI displays
    that and only ever uses the key to mint signed URLs. Deterministic:
    same name -> same key (idempotent with x-upsert); different names ->
    different hashes -> no collisions."""
    m = _EXT_RE.search(filename or "")
    ext = ("." + m.group(1).lower()) if m else ""
    digest = hashlib.sha1((filename or "").encode("utf-8")).hexdigest()[:16]
    return f"{email_id}/{digest}{ext}"


def is_expired(fetched_at, now) -> bool:
    """Retention cutoff for one attachment (upload-time based)."""
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return fetched_at < now - timedelta(days=RETENTION_DAYS)


def extract_attachments(msg: email.message.Message) -> list[dict]:
    """Attachment parts: disposition=='attachment' OR a pdf/office MIME type
    with a filename. Returns [{filename, content_type, payload}]."""
    out = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        raw_name = part.get_filename()
        ctype = part.get_content_type()
        is_attach = part.get_content_disposition() == "attachment"
        is_doc = any(ctype.startswith(p) for p in DOC_MIME_PREFIXES)
        if not raw_name or not (is_attach or is_doc):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        out.append({
            "filename": sanitize_filename(decode_mime_header(raw_name)),
            "content_type": ctype,
            "payload": payload,
        })
    return out


def _upload_to_storage(cfg: tuple[str, str], path: str, payload: bytes, content_type: str) -> bool:
    """POST the bytes to the private bucket. x-upsert makes re-runs overwrite
    in place instead of erroring. Fail-soft: False on any failure (logged
    WITHOUT the key), the caller skips the metadata row so a later run/backfill
    can retry."""
    url = f"{cfg[0]}/storage/v1/object/{BUCKET}/{quote(path)}"
    log.info("EMAIL storage upload %s", path)
    try:
        r = requests.post(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {cfg[1]}",
                "Content-Type": content_type or "application/octet-stream",
                "x-upsert": "true",
            },
            timeout=60,
        )
    except Exception as e:
        log.warning("storage upload failed for %s: %s", path, e)
        return False
    if r.status_code not in (200, 201):
        log.warning("storage upload for %s -> HTTP %s", path, r.status_code)
        return False
    return True


def save_attachments(engine, cfg, email_id: int, atts: list[dict]) -> tuple[int, int]:
    """Upload + record metadata for one email. Returns (saved, oversize).
    Oversize files keep a metadata row with storage_path NULL (a permanent
    "exists but not stored" marker); failed uploads write NOTHING so they stay
    retryable. The object key is ASCII-derived via storage_key() (Storage 400s
    non-ASCII keys); `filename` keeps the original (Hebrew) name for display."""
    saved = oversize = 0
    for att in atts:
        path = storage_key(email_id, att["filename"])
        values = {
            "email_id": email_id,
            "filename": att["filename"],
            "size_bytes": len(att["payload"]),
            "content_type": att["content_type"],
            "storage_path": None,
        }
        if len(att["payload"]) > MAX_ATTACHMENT_BYTES:
            oversize += 1
            log.info("  attachment %s: %.1f MB > cap — metadata only, file skipped",
                     path, len(att["payload"]) / 1e6)
        else:
            if not _upload_to_storage(cfg, path, att["payload"], att["content_type"]):
                continue  # retryable — no row written
            values["storage_path"] = path
        with engine.begin() as conn:
            if conn.execute(_INSERT_ATTACHMENT, values).rowcount and values["storage_path"]:
                saved += 1
    return saved, oversize


def prune_attachments(engine, cfg) -> int:
    """Retention sweep: delete Storage objects AND metadata rows older than
    RETENTION_DAYS (bounded batch). If the Storage delete fails, rows are kept
    so the next run retries — never orphan an object by dropping its row."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    with engine.connect() as conn:
        rows = conn.execute(
            text("select id, storage_path from email_attachments where fetched_at < :cutoff limit 500"),
            {"cutoff": cutoff},
        ).all()
    if not rows:
        return 0
    paths = [p for _, p in rows if p]
    if paths:
        try:
            r = requests.delete(
                f"{cfg[0]}/storage/v1/object/{BUCKET}",
                json={"prefixes": paths},
                headers={"Authorization": f"Bearer {cfg[1]}"},
                timeout=60,
            )
            if r.status_code != 200:
                log.warning("retention: storage delete -> HTTP %s — keeping rows for retry", r.status_code)
                return 0
        except Exception as e:
            log.warning("retention: storage delete failed: %s — keeping rows for retry", e)
            return 0
    ids = [i for i, _ in rows]
    stmt = text("delete from email_attachments where id in :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    with engine.begin() as conn:
        conn.execute(stmt, {"ids": ids})
    return len(rows)


def decode_mime_header(raw: str | None) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def extract_body_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        plain, html = None, None
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            if ctype == "text/plain" and plain is None:
                plain = part.get_payload(decode=True)
                plain_charset = part.get_content_charset() or "utf-8"
                plain = plain.decode(plain_charset, errors="replace")
            elif ctype == "text/html" and html is None:
                html = part.get_payload(decode=True)
                html_charset = part.get_content_charset() or "utf-8"
                html = html.decode(html_charset, errors="replace")
        if plain:
            return plain
        if html:
            return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        return ""
    else:
        payload = msg.get_payload(decode=True)
        if payload is None:
            return ""
        charset = msg.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        if msg.get_content_type() == "text/html":
            return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
        return text


# --------------------------------------------------------------------------- #
# Attribution ladder (Phase 8) — skip, don't fabricate.                        #
# Wrong attribution is worse than none: an email that cannot be CONFIDENTLY    #
# attributed gets sec_id NULL, which the UI already renders as macro.          #
# The old sender tier is deleted: it substring-matched Citigroup's symbol 'C'  #
# against the 'c' in every ".com" sender address, tagging ALL email as C.      #
# --------------------------------------------------------------------------- #

# Generic corporate tokens that must never identify a security on their own —
# בנק would match every bank, אנרגיה every energy company. Checked against
# NORMALIZED tokens (quotes/gershayim removed: בע"מ -> בעמ, נדל"ן -> נדלן).
# ADD FREELY as new false-positive tokens surface in the ambiguity warnings.
NOISE_TOKENS = {
    # Hebrew (normalized forms)
    "בעמ", "בנק", "מערכות", "תעשיות", "תעשייה", "החזקות", "אחזקות", "הולדינגס",
    "השקעות", "קבוצה", "קבוצת", "ישראל", "לישראל", "נדלן", "אנרגיה", "גרופ",
    "אינטרנשיונל", "טכנולוגיות", "בית", "חברה", "חברת", "פיננסים", "שותפות",
    # English (lowercased)
    "ltd", "inc", "corp", "corporation", "company", "co", "plc", "group",
    "holdings", "holding", "industries", "international", "the", "and", "of",
    "sa", "se", "ag", "nv", "adr", "class", "technologies", "enterprises",
}

# Strip quote-like marks INSIDE words before tokenizing, so בע"מ / בע״מ -> בעמ
# and ויז'ן / ויז׳ן -> ויזן — the same normalization applies to the email text
# and the security name, so both sides agree.
_QUOTES_RE = re.compile("[\"'`׳״‘’“”]")
_NON_TOKEN_RE = re.compile(r"[^0-9A-Za-z֐-׿]+")

# Symbols shorter than 2 chars are NEVER text-matched. This is the C
# catastrophe, structurally: a 1-letter ticker as a match needle tagged every
# email in the inbox as Citigroup. Do NOT "simplify" this guard away.
MIN_SYMBOL_LEN = 2
# Name tokens shorter than 3 chars are too generic to identify a company.
MIN_NAME_TOKEN_LEN = 3


def _tokens(text: str) -> set[str]:
    """Whole-word tokens: quotes stripped in-word, everything else split on
    non-alphanumerics, English lowercased, Hebrew kept as-is, len>=2."""
    if not text:
        return set()
    t = _QUOTES_RE.sub("", text)
    t = _NON_TOKEN_RE.sub(" ", t).lower()
    return {w for w in t.split() if len(w) >= 2}


def _distinctive_name_tokens(name: str) -> set[str]:
    return {
        w for w in _tokens(name)
        if w not in NOISE_TOKENS and len(w) >= MIN_NAME_TOKEN_LEN and not w.isdigit()
    }


def _symbol_needles(sec: dict) -> set[str]:
    """Text-matchable ticker aliases: symbol and yahoo_symbol prefix (LUMI from
    LUMI.TA). Pure digits are excluded (tier 1 owns numbers); len>=MIN_SYMBOL_LEN."""
    out = set()
    for raw in (sec.get("symbol"), sec.get("yahoo_symbol")):
        if not raw:
            continue
        needle = str(raw).split(".")[0].lower()
        if len(needle) >= MIN_SYMBOL_LEN and not needle.isdigit():
            out.add(needle)
    return out


# Bloomberg auto-alerts: the subject is ALWAYS "<TICKER> <CC> Equity ..." — a
# per-stock analyst-estimate alert, never macro, never about an ETF's index.
_BBG_SUBJECT_RE = re.compile(r"^\s*(\S+)\s+([A-Z]{2})\s+Equity\b")

# Bloomberg's OWN exchange codes -> our yahoo-style suffix. Bloomberg writes
# "DSY FP Equity"; we store DSY.PA — the mismatch is why 124 Dassault alerts sat
# unattributed. Table-driven: add a line when a new code shows up. An UNMAPPED
# code simply falls through (no candidate is built) — deliberately no bare-ticker
# fallback, since "AAPL CN" is a different company from our AAPL.
# US VENUE CODES all mean the same bare ticker: US=composite, UN=NYSE,
# UW=NASDAQ, UQ/UR=NASDAQ tiers, UA=NYSE American, UP=NYSE Arca. Bloomberg
# picks whichever venue the alert came from, so ICE UN / TW UW are our ICE / TW.
BBG_SUFFIX = {
    "US": "", "UN": "", "UW": "", "UQ": "", "UR": "", "UA": "", "UP": "",
    "LN": ".L",    # London
    "FP": ".PA",   # Paris
    "GY": ".DE",   # Xetra / Germany
    "IM": ".MI",   # Milan
    "SM": ".MC",   # Madrid
    "NA": ".AS",   # Amsterdam
    "BB": ".BR",   # Brussels
    "SS": ".ST",   # Stockholm
    "SW": ".SW",   # SIX Swiss
    "JT": ".T",    # Tokyo
    "JP": ".T",    # Tokyo (alt code)
    "HK": ".HK",   # Hong Kong
    "TT": ".TW",   # Taiwan
    "KP": ".KS",   # Korea (KOSPI)
    "AU": ".AX",   # Australia (ASX)
    "CN": ".TO",   # Canada (Toronto)
    "SJ": ".JO",   # Johannesburg
}


# The 4th Bloomberg shape: no ticker, no "Equity" — "<COMPANY NAME>: Files 4",
# "<COMPANY NAME>: Target Px increased to 224 AUD ... by Macquarie". Bloomberg
# writes the name in CAPS and TRUNCATES it to ~28 chars ("TAIWAN SEMICONDUCTOR
# MANUFAC"), so matching is prefix-based, not exact.
_BBG_NAME_RE = re.compile(r"^(?P<name>[A-Z][^:]{2,60}):\s+\S")

# Dropped from the TAIL of both sides before comparing — they carry no identity
# ("Japan Exchange Group, Inc." and "JAPAN EXCHANGE GROUP INC" must agree).
_NAME_SUFFIXES = {"INC", "CORP", "CORPORATION", "LTD", "LIMITED", "PLC", "SE", "SA",
                  "NV", "AG", "CO", "GROUP", "HOLDINGS", "HOLDING", "CLASS", "A", "REG"}
_NAME_PUNCT_RE = re.compile(r"[.,&/\-]+")
# Below this length a PREFIX is not evidence ("APPLE" would claim APPLEBEES), so
# short names fall back to exact equality instead — which is safe at any length,
# and keeps a tracked short-named security (APPLE/VISA/EBAY/SANO) from being
# dropped as untracked just for being short.
MIN_BBG_NAME_CHARS = 8


def normalize_company(name: str) -> str:
    """UPPERCASE, punctuation -> space, whitespace collapsed, trailing corporate
    suffixes dropped: 'Japan Exchange Group, Inc.' -> 'JAPAN EXCHANGE'."""
    words = _NAME_PUNCT_RE.sub(" ", (name or "").upper()).split()
    while words and words[-1] in _NAME_SUFFIXES:
        words.pop()
    return " ".join(words)


def _name_prefix_match(a: str, b: str) -> bool:
    """Prefix match in BOTH directions (Bloomberg truncates; our names are full)
    once the SHORTER side clears the significant-length floor; below it, exact
    equality only."""
    if not a or not b:
        return False
    if len(min(a, b, key=len).replace(" ", "")) < MIN_BBG_NAME_CHARS:
        return a == b
    return a.startswith(b) or b.startswith(a)


def is_bbg_company_subject(subject: str) -> bool:
    """The CAPS company-name shape. The no-lowercase guard is deliberate: without
    it, an ordinary newsletter subject ('Morning Brief: markets rally') matches
    the pattern and would be dropped as an untracked company."""
    m = _BBG_NAME_RE.match(subject or "")
    return bool(m) and not any(c.islower() for c in m.group("name"))


def is_bbg_stock_alert(subject: str) -> bool:
    """True for the Bloomberg per-stock subject shape (regardless of exchange)."""
    return _BBG_SUBJECT_RE.match(subject or "") is not None


def bbg_candidate(subject: str) -> str | None:
    """'DSY FP Equity Standard BEst updated for 2026' -> 'DSY.PA'. None when the
    subject isn't that shape, or its exchange code isn't in BBG_SUFFIX."""
    m = _BBG_SUBJECT_RE.match(subject or "")
    if not m:
        return None
    suffix = BBG_SUFFIX.get(m.group(2))
    return None if suffix is None else m.group(1) + suffix


def _resolve(hits: list[dict], tier: str) -> tuple[str | None, str | None] | None:
    """One hit wins; several = ambiguous -> NULL, loudly; none = try next scope."""
    if len(hits) == 1:
        return hits[0]["sec_id"], tier
    if len(hits) > 1:
        log.warning(
            "attribution ambiguous (%s): %s — leaving unattributed (macro)",
            tier, [(h["sec_id"], h["name"]) for h in hits],
        )
        return None, None
    return None


def attribute_email(subject: str, body: str, secs: list[dict]):
    """The confidence ladder. Returns (sec_id, matched_by) or (None, None).

    Tiers, strict order — subject outranks body within each tier:
      0. 'bbg'    — a Bloomberg per-stock subject, mapped exchange code -> our
                    symbol. EXCLUSIVE: if the subject has that shape, no lower
                    tier may run, because the body of an alert about an
                    untracked stock routinely names OTHER companies and would
                    mis-attribute. Miss here = (None, None), and the caller
                    drops the message entirely (it is not macro).
      1. 'secnum' — a 6-9 digit TASE security number as a standalone token
      2. 'symbol' — an English ticker as a whole word (len>=2; 1-letter
                    symbols like C are structurally excluded)
      3. 'name'   — at least one DISTINCTIVE name token (noise stripped)
    Multi-match at any tier -> (None, None) + warning. No match -> (None, None):
    NULL sec_id is the legitimate macro home, never a guess.
    """
    cand = bbg_candidate(subject)
    if cand:  # tier 0: Bloomberg ticker+exchange -> our symbol (whole match)
        want = cand.lower()
        hits = [s for s in secs
                if want in {str(s.get("symbol") or "").lower(), str(s.get("yahoo_symbol") or "").lower()}]
        return _resolve(hits, "bbg") or (None, None)
    if is_bbg_company_subject(subject):
        # tier 0b: Bloomberg CAPS company name -> our securities.name, both
        # normalized. EXCLUSIVE like the ticker tier: the alert text ("Volume
        # Since Open", "Target Px increased") is common English that collides
        # with company names, which is exactly how LSEG once became LPRO.
        want = normalize_company(_BBG_NAME_RE.match(subject).group("name"))
        hits = [s for s in secs if _name_prefix_match(want, normalize_company(s.get("name")))]
        return _resolve(hits, "bbgname") or (None, None)
    if is_bbg_stock_alert(subject):
        # UNMAPPED exchange code: no bare-ticker guess — 'AAPL CN' is a foreign
        # twin, not our AAPL, and a wrong attribution is worse than none. The
        # email is KEPT as NULL (collect warns + counts) and the null-sweep will
        # claim it for free once the code is added to BBG_SUFFIX.
        return None, None

    scopes = [_tokens(subject), _tokens(body)]

    for scope in scopes:  # tier 1: security number
        hits = [
            s for s in secs
            if str(s["sec_id"]).isdigit() and len(str(s["sec_id"])) >= 6 and str(s["sec_id"]) in scope
        ]
        result = _resolve(hits, "secnum")
        if result is not None:
            return result

    for scope in scopes:  # tier 2: ticker as a whole word
        hits = [s for s in secs if _symbol_needles(s) & scope]
        result = _resolve(hits, "symbol")
        if result is not None:
            return result

    for scope in scopes:  # tier 3: distinctive name tokens
        hits = [s for s in secs if _distinctive_name_tokens(s["name"] or "") & scope]
        result = _resolve(hits, "name")
        if result is not None:
            return result

    return None, None


SWEEP_BATCH = 200


def reattribute_nulls(engine, secs: list[dict]) -> int:
    """NULL-only sweep: re-run the ladder over emails with sec_id IS NULL, so a
    security added AFTER an email arrived can still claim it. Never rewrites a
    non-NULL sec_id — the SELECT filters on NULL and the UPDATE re-checks it."""
    stmt = (
        select(emails.c.id, emails.c.subject, emails.c.body_text)
        .where(emails.c.sec_id.is_(None))
        .order_by(emails.c.id.desc())
        .limit(SWEEP_BATCH)
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    filled = 0
    for eid, subject, body in rows:
        sec_id, matched_by = attribute_email(subject or "", body or "", secs)
        if sec_id is None:
            continue
        with engine.begin() as conn:
            result = conn.execute(
                update(emails)
                .where(emails.c.id == eid, emails.c.sec_id.is_(None))
                .values(sec_id=sec_id, matched_by=matched_by)
            )
        filled += result.rowcount or 0
    if rows:
        log.info("null-sweep: scanned=%d re-attributed=%d", len(rows), filled)
    return filled


def collect() -> None:
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_pass:
        log.info("GMAIL_USER / GMAIL_APP_PASSWORD not set — skipping email collection (clean no-op).")
        return

    # After the Gmail no-op check on purpose: local dev without mail creds
    # never reaches this, so the collector stays a clean no-op there; in CI a
    # forgotten secret fails loudly (the sec_ids pattern).
    storage_cfg = _storage_config()

    engine = get_engine()
    init_db(engine)
    with engine.connect() as conn:
        secs = [dict(row._mapping) for row in conn.execute(select(securities))]

    # Before touching the inbox: give previously-unattributed emails a chance
    # to match securities that were added after they arrived.
    reattribute_nulls(engine, secs)
    retention_deleted = prune_attachments(engine, storage_cfg)

    # Explicit connect timeout (IMAP4_SSL supports it on 3.9+; CI is 3.12) —
    # the run has hung here for 28m+ with zero output. Log first so a future
    # hang shows WHERE it stopped.
    log.info("EMAIL connecting imap.gmail.com")
    imap = imaplib.IMAP4_SSL(IMAP_HOST, timeout=60)
    try:
        log.info("EMAIL login")
        imap.login(gmail_user, gmail_pass)
        log.info("EMAIL select")
        imap.select("INBOX")
        log.info("EMAIL search")
        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            log.warning("IMAP search failed: %s", status)
            return
        ids = data[0].split()
        log.info("EMAIL search n=%d", len(ids))

        fetched = new_count = dup_count = tagged = 0
        attachments_saved = skipped_oversize = skipped_untracked_stock = bbg_unmapped_code = 0
        by_tier = {"bbg": 0, "bbgname": 0, "secnum": 0, "symbol": 0, "name": 0}
        for i, msg_id in enumerate(ids, 1):
            try:
                log.info("EMAIL fetch %d/%d", i, len(ids))
                status, msg_data = imap.fetch(msg_id, "(BODY.PEEK[])")
                if status != "OK" or not msg_data or msg_data[0] is None:
                    log.warning("fetch failed for id %s", msg_id)
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                sender = decode_mime_header(msg.get("From", ""))
                subject = decode_mime_header(msg.get("Subject", ""))
                message_id = msg.get("Message-ID") or f"<uid-{msg_id.decode()}@{gmail_user}>"
                date_raw = msg.get("Date")
                received_at = None
                if date_raw:
                    try:
                        received_at = parsedate_to_datetime(date_raw)
                    except (TypeError, ValueError):
                        received_at = None
                body_text = extract_body_text(msg)
                sec_id, matched_by = attribute_email(subject, body_text, secs)
                # A Bloomberg per-stock alert we can't map to a watched security
                # is about a stock we don't track — it is NOT macro, so it is
                # dropped instead of polluting the macro tab. Marked \\Seen: a
                # deliberate skip is a completed outcome, not a failure to retry.
                if sec_id is None and is_bbg_stock_alert(subject):
                    cc = _BBG_SUBJECT_RE.match(subject).group(2)
                    if cc in BBG_SUFFIX:
                        skipped_untracked_stock += 1
                        log.info("  %r -> BBG per-stock alert, untracked — skipped", subject[:60])
                        imap.store(msg_id, "+FLAGS", "\\Seen")
                        continue
                    # DROPPING MAIL REQUIRES CERTAINTY, and an unknown exchange
                    # code is not certainty — the security may be tracked under a
                    # suffix we haven't mapped. Keep it (NULL = macro), shout the code.
                    bbg_unmapped_code += 1
                    log.warning("BBG unmapped country code %s (subject: %r)", cc, subject[:60])
                elif sec_id is None and is_bbg_company_subject(subject):
                    # Same rule as the ticker shape: a per-company alert we can't
                    # map is about a company we don't track — never macro.
                    skipped_untracked_stock += 1
                    log.info("  %r -> BBG company alert, untracked — skipped", subject[:60])
                    imap.store(msg_id, "+FLAGS", "\\Seen")
                    continue
                if sec_id:
                    tagged += 1
                    by_tier[matched_by] += 1
                log.info(
                    "  %r -> %s", subject[:60],
                    f"{sec_id} ({matched_by})" if sec_id else "unattributed (macro)",
                )

                stmt = insert_ignore(engine, emails, ["message_id"]).values(
                    sec_id=sec_id,
                    sender=sender,
                    subject=subject,
                    received_at=received_at,
                    body_text=body_text,
                    matched_by=matched_by,
                    message_id=message_id,
                )
                with engine.begin() as conn:
                    result = conn.execute(stmt)
                fetched += 1
                if result.rowcount:
                    new_count += 1
                else:
                    dup_count += 1

                # Attachments: best-effort, fail-soft — a failed upload never
                # blocks the mail pipeline (a metadata row is only written on
                # success, so the email_backfill CLI can retry later).
                atts = extract_attachments(msg)
                if atts:
                    with engine.connect() as conn:
                        eid = conn.execute(_EMAIL_ID_BY_MSGID, {"message_id": message_id}).scalar()
                    if eid is not None:
                        s, o = save_attachments(engine, storage_cfg, eid, atts)
                        attachments_saved += s
                        skipped_oversize += o

                imap.store(msg_id, "+FLAGS", "\\Seen")
            except Exception as e:
                log.warning("failed processing message id %s: %s", msg_id, e)
                continue

        # The summary that measures real-world attribution recall, per tier —
        # the collect_enrich pattern. Ambiguous cases appear as WARNINGs above.
        log.info(
            "done: fetched=%d new=%d duplicate=%d attributed=%d (bbg=%d bbgname=%d secnum=%d symbol=%d name=%d) none=%d "
            "skipped_untracked_stock=%d bbg_unmapped_code=%d attachments_saved=%d skipped_oversize=%d "
            "retention_deleted=%d",
            fetched, new_count, dup_count, tagged,
            by_tier["bbg"], by_tier["bbgname"], by_tier["secnum"], by_tier["symbol"], by_tier["name"],
            fetched - tagged,
            skipped_untracked_stock, bbg_unmapped_code, attachments_saved, skipped_oversize, retention_deleted,
        )
    finally:
        try:
            imap.logout()
        except Exception:
            pass


if __name__ == "__main__":
    collect()
