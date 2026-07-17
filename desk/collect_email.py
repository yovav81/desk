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
    retryable. Collision-safe path: {email_id}/{sanitized filename} — the DB id
    avoids message_id's <>@ characters."""
    saved = oversize = 0
    for att in atts:
        path = f"{email_id}/{att['filename']}"
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
      1. 'secnum' — a 6-9 digit TASE security number as a standalone token
      2. 'symbol' — an English ticker as a whole word (len>=2; 1-letter
                    symbols like C are structurally excluded)
      3. 'name'   — at least one DISTINCTIVE name token (noise stripped)
    Multi-match at any tier -> (None, None) + warning. No match -> (None, None):
    NULL sec_id is the legitimate macro home, never a guess.
    """
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

    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        imap.login(gmail_user, gmail_pass)
        imap.select("INBOX")
        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            log.warning("IMAP search failed: %s", status)
            return
        ids = data[0].split()
        log.info("unseen messages: %d", len(ids))

        fetched = new_count = dup_count = tagged = 0
        attachments_saved = skipped_oversize = 0
        by_tier = {"secnum": 0, "symbol": 0, "name": 0}
        for msg_id in ids:
            try:
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
            "done: fetched=%d new=%d duplicate=%d attributed=%d (secnum=%d symbol=%d name=%d) none=%d "
            "attachments_saved=%d skipped_oversize=%d retention_deleted=%d",
            fetched, new_count, dup_count, tagged,
            by_tier["secnum"], by_tier["symbol"], by_tier["name"], fetched - tagged,
            attachments_saved, skipped_oversize, retention_deleted,
        )
    finally:
        try:
            imap.logout()
        except Exception:
            pass


if __name__ == "__main__":
    collect()
