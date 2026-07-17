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
from email.header import decode_header
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup
from sqlalchemy import select, update

from desk.db import emails, get_engine, init_db, insert_ignore, securities

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_email")

IMAP_HOST = "imap.gmail.com"


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

    engine = get_engine()
    init_db(engine)
    with engine.connect() as conn:
        secs = [dict(row._mapping) for row in conn.execute(select(securities))]

    # Before touching the inbox: give previously-unattributed emails a chance
    # to match securities that were added after they arrived.
    reattribute_nulls(engine, secs)

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

                imap.store(msg_id, "+FLAGS", "\\Seen")
            except Exception as e:
                log.warning("failed processing message id %s: %s", msg_id, e)
                continue

        # The summary that measures real-world attribution recall, per tier —
        # the collect_enrich pattern. Ambiguous cases appear as WARNINGs above.
        log.info(
            "done: fetched=%d new=%d duplicate=%d attributed=%d (secnum=%d symbol=%d name=%d) none=%d",
            fetched, new_count, dup_count, tagged,
            by_tier["secnum"], by_tier["symbol"], by_tier["name"], fetched - tagged,
        )
    finally:
        try:
            imap.logout()
        except Exception:
            pass


if __name__ == "__main__":
    collect()
