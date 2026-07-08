"""Collect emails from a dedicated Gmail inbox via IMAP into the DB.

Raw data only: stores body text and best-effort company tagging, no
summarization. Never deletes or moves mail. Messages are marked \\Seen only
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
from email.header import decode_header
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup
from sqlalchemy import select

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


def tag_security(sender: str, subject: str, body: str, secs: list[dict]):
    """Best-effort match against sender / subject / body. Returns (sec_id, matched_by) or (None, None)."""
    sender_l = sender.lower()
    subject_l = subject.lower()
    body_l = body.lower()
    for sec in secs:
        needle = sec["symbol"].split(".")[0].lower()
        if needle and needle in sender_l:
            return sec["sec_id"], "sender"
    for sec in secs:
        name_l = sec["name"].lower()
        if name_l and name_l in subject_l:
            return sec["sec_id"], "subject"
        if sec["symbol"].lower() in subject_l:
            return sec["sec_id"], "subject"
    for sec in secs:
        name_l = sec["name"].lower()
        if name_l and name_l in body_l:
            return sec["sec_id"], "body"
    return None, None


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
                sec_id, matched_by = tag_security(sender, subject, body_text, secs)
                if sec_id:
                    tagged += 1

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

        log.info("done: fetched=%d new=%d duplicate=%d tagged=%d", fetched, new_count, dup_count, tagged)
    finally:
        try:
            imap.logout()
        except Exception:
            pass


if __name__ == "__main__":
    collect()
