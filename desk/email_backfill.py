"""One-shot backfill: fetch ATTACHMENTS for emails collected before attachment
support existed (Phase 8 step 5). The collector never deletes mail and stores
message_id, so each row's original message is re-fetched from IMAP by its
Message-ID header and only the attachments are extracted — the email row and
body_text are never modified.

Reuses collect_email's extraction/upload/sanitization verbatim (one
implementation, no drift). Storage config and the 20 MB cap come from there
too. Requires GMAIL_USER / GMAIL_APP_PASSWORD and SUPABASE_URL /
SUPABASE_SERVICE_ROLE_KEY (the latter is a GitHub-Secrets-only key that
bypasses RLS — never logged, never printed).

CLI (SAFE BY DEFAULT — dry-run unless --commit, mirroring sec_ids):
    python -m desk.email_backfill            # dry-run: report what would be saved
    python -m desk.email_backfill --commit   # upload + write metadata rows
Idempotent: emails that already have attachment rows are excluded by the
query, and ON CONFLICT (email_id, filename) + x-upsert guard the rest.
"""
import argparse
import email
import imaplib
import logging
import os
import sys
import time

from sqlalchemy import text

from desk.collect_email import (
    IMAP_HOST,
    MAX_ATTACHMENT_BYTES,
    _storage_config,
    extract_attachments,
    save_attachments,
)
from desk.db import get_engine, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("email_backfill")

# One-shot tool, but still bounded: a huge inbox backfills over several runs
# rather than hammering IMAP/Storage in one sitting.
MAX_PER_RUN = 50

# Emails with NO attachment rows yet. Ones whose messages genuinely have no
# attachments will be re-scanned on every run of this tool — acceptable for a
# one-shot CLI (it is not on the cron).
_CANDIDATES = text(
    "select id, message_id, subject from emails"
    " where id not in (select email_id from email_attachments)"
    " order by id desc limit :n"
)


def backfill(commit: bool = False) -> None:
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_pass:
        raise SystemExit("GMAIL_USER / GMAIL_APP_PASSWORD not set — required to re-fetch messages.")
    storage_cfg = _storage_config()  # SystemExit (naming the vars) if missing

    engine = get_engine()
    init_db(engine)
    with engine.connect() as conn:
        rows = conn.execute(_CANDIDATES, {"n": MAX_PER_RUN}).all()
    log.info(
        "emails without attachment rows: %d (cap %d)%s",
        len(rows), MAX_PER_RUN, "" if commit else "  [DRY-RUN — no writes]",
    )
    if not rows:
        return

    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    scanned = with_atts = saved = oversize = not_found = 0
    try:
        imap.login(gmail_user, gmail_pass)
        imap.select("INBOX")
        for eid, message_id, subject in rows:
            scanned += 1
            try:
                # Message-ID contains <>@ — IMAP wants it quoted.
                status, data = imap.search(None, "HEADER", "Message-ID", f'"{message_id}"')
                ids = data[0].split() if status == "OK" and data and data[0] else []
                if not ids:
                    not_found += 1
                    log.info("  #%d %r: message not found in inbox", eid, (subject or "")[:50])
                    continue
                status, msg_data = imap.fetch(ids[0], "(BODY.PEEK[])")
                if status != "OK" or not msg_data or msg_data[0] is None:
                    not_found += 1
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                atts = extract_attachments(msg)
                if not atts:
                    log.info("  #%d %r: no attachments", eid, (subject or "")[:50])
                    continue
                with_atts += 1
                if commit:
                    s, o = save_attachments(engine, storage_cfg, eid, atts)
                    saved += s
                    oversize += o
                else:
                    for a in atts:
                        verdict = "OVERSIZE (metadata only)" if len(a["payload"]) > MAX_ATTACHMENT_BYTES else "would save"
                        log.info("  #%d would process %s (%.1f MB) — %s",
                                 eid, a["filename"], len(a["payload"]) / 1e6, verdict)
            except Exception as e:
                log.warning("  #%d failed: %s", eid, e)
            time.sleep(0.3)  # gentle on IMAP
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    log.info(
        "%s: scanned=%d with_attachments=%d saved=%d oversize=%d not_found=%d",
        "done" if commit else "DRY-RUN done (nothing written; re-run with --commit)",
        scanned, with_atts, saved, oversize, not_found,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m desk.email_backfill",
        description="One-shot: fetch attachments for pre-existing emails from IMAP.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="report what would be saved, write nothing (DEFAULT)")
    mode.add_argument("--commit", action="store_true",
                      help="actually upload files + write metadata rows")
    args = parser.parse_args()
    backfill(commit=args.commit)


if __name__ == "__main__":
    sys.exit(main())
