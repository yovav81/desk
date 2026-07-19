"""Collect news for every security on any user's watchlist.

Routing by market: US -> Finnhub company-news (FINNHUB_API_KEY; falls back to
Google News if the key is missing); TASE/GLOBAL -> Google News RSS.

Raw data only: no LLM calls, no summarization. `summary` is always left NULL.
Dedup guard: news.url is UNIQUE; INSERT ... ON CONFLICT(url) DO NOTHING.

For TASE securities, results are best when data/securities.csv's `name`
column is in Hebrew (Google News hl/gl is set to he/IL for market=TASE).
"""
import json
import logging
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from sqlalchemy import select

from desk.db import get_engine, init_db, insert_ignore, news, securities, watchlist

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_news")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeskCollector/0.1"

# Ingest staleness gate. Google News occasionally surfaces ARCHIVE items — a
# SAP article dated 2026-01-05 arrived in the 2026-07-17 07:00 run and sank to
# ~position 1400 of the published_at-sorted feed. That is archive noise, not
# news: anything PROVABLY older than this many days at collection time is
# skipped and counted, never inserted. Items with no parseable date are NOT
# stale (we only act on proof) and keep the existing behaviour: stored with
# published_at NULL. Ingest-time only — existing rows are never touched (the
# one Jan-05 row already stored stays; one row isn't worth a migration).
STALE_DAYS = 7


def is_stale(published_at, now) -> bool:
    """True only for a PROVABLY old article. None -> False (not provably old).
    A naive timestamp (RFC 2822 '-0000') is compared as UTC — comparison only,
    the stored value is whatever the feed said."""
    if published_at is None:
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return published_at < now - timedelta(days=STALE_DAYS)


# Near-duplicate suppression (Phase 12D): the same story now arrives from
# several sources with different URLs and slightly different titles, so the
# UNIQUE(url) guard can't catch it. Token-set Jaccard within a 72h window,
# grouped per sec_id (macro = the NULL group). Skips are counted, never deleted.
SIMILAR_HOURS = 72


def norm_tokens(title: str) -> set[str]:
    """Punctuation/quotes/brackets stripped, latin lowercased, whitespace
    tokens of len>=2 (Hebrew is untouched by lower())."""
    cleaned = re.sub(r"[^\w\s]|_", " ", (title or "").lower())
    return {t for t in cleaned.split() if len(t) >= 2}


def is_similar(a_tokens: set[str], b_tokens: set[str]) -> bool:
    """Jaccard >= 0.75 AND at least 4 shared tokens."""
    inter = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)
    return union > 0 and inter / union >= 0.75 and inter >= 4


def recent_title_groups(engine, now) -> dict:
    """ONE query per run: last SIMILAR_HOURS of (sec_id, title) as token sets
    grouped by sec_id. Rows with published_at NULL are outside the window."""
    stmt = select(news.c.sec_id, news.c.title).where(
        news.c.published_at >= now - timedelta(hours=SIMILAR_HOURS)
    )
    groups: dict = {}
    with engine.connect() as conn:
        for sec_id, title in conn.execute(stmt):
            groups.setdefault(sec_id, []).append(norm_tokens(title))
    return groups


def watchlisted_securities(engine) -> list[dict]:
    """Distinct securities across the UNION of all users' watchlists."""
    stmt = select(securities).join(watchlist, watchlist.c.sec_id == securities.c.sec_id).distinct()
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(stmt)]


def rss_url_for(sec: dict) -> str:
    is_tase = sec["market"] == "TASE"
    hl, gl = ("he", "IL") if is_tase else ("en", "US")
    q = quote(f'"{sec["name"]}"')
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={gl}:{hl}"


def fetch_feed(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    root = ET.fromstring(raw)
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        pub_raw = item.findtext("pubDate")
        published_at = None
        if pub_raw:
            try:
                published_at = parsedate_to_datetime(pub_raw)
            except (TypeError, ValueError):
                published_at = None
        if title and link:
            items.append({"title": title, "url": link, "published_at": published_at})
    return items


def fetch_finnhub(symbol: str, key: str, now: datetime) -> list[dict]:
    """Finnhub company-news, last STALE_DAYS days. datetime is unix seconds UTC."""
    frm = (now - timedelta(days=STALE_DAYS)).date().isoformat()
    url = (
        "https://finnhub.io/api/v1/company-news"
        f"?symbol={quote(symbol)}&from={frm}&to={now.date().isoformat()}&token={key}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    items = []
    for it in data:
        title, link, ts = it.get("headline"), it.get("url"), it.get("datetime")
        published_at = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        if title and link:
            items.append({"title": title, "url": link, "published_at": published_at})
    return items


def collect() -> None:
    engine = get_engine()
    init_db(engine)
    secs = watchlisted_securities(engine)
    log.info("securities on watchlists: %d", len(secs))

    now = datetime.now(timezone.utc)
    finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
    if not finnhub_key and any(s["market"] == "US" for s in secs):
        log.warning("NEWS finnhub key missing — falling back to Google News for US")

    groups = recent_title_groups(engine, now)
    total_read = total_inserted = total_dup = total_stale = total_similar = 0
    for sec in secs:
        use_finnhub = sec["market"] == "US" and bool(finnhub_key)
        try:
            if use_finnhub:
                items = fetch_finnhub(sec["symbol"], finnhub_key, now)
            else:
                items = fetch_feed(rss_url_for(sec))
        except urllib.error.HTTPError as e:
            log.warning("finnhub HTTP %d for %s (%s) — skipped" if use_finnhub
                        else "feed HTTP %d for %s (%s) — skipped",
                        e.code, sec["sec_id"], sec["name"])
            continue
        except Exception as e:
            log.warning("feed failed for %s (%s): %s", sec["sec_id"], sec["name"], e)
            continue

        # Truthful vocabulary (the maya-style "new=" ambiguity burned us twice):
        # inserted= is the LITERAL insert count — ON CONFLICT DO NOTHING reports
        # rowcount 0 on a duplicate — and duplicate=/skipped_stale= name the
        # other two outcomes explicitly.
        group = groups.setdefault(sec["sec_id"], [])
        inserted = stale = similar = 0
        with engine.begin() as conn:
            for it in items:
                if is_stale(it["published_at"], now):
                    stale += 1
                    continue
                toks = norm_tokens(it["title"])
                if any(is_similar(toks, t) for t in group):
                    similar += 1
                    continue
                stmt = insert_ignore(engine, news, ["url"]).values(
                    sec_id=sec["sec_id"],
                    source="finnhub" if use_finnhub else "google_news",
                    title=it["title"],
                    url=it["url"],
                    published_at=it["published_at"],
                    summary=None,
                )
                result = conn.execute(stmt)
                if result.rowcount:
                    inserted += 1
                    group.append(toks)

        dup = len(items) - inserted - stale - similar
        total_read += len(items)
        total_inserted += inserted
        total_dup += dup
        total_stale += stale
        total_similar += similar
        log.info(
            "%s (%s): read=%d inserted=%d duplicate=%d skipped_stale=%d skipped_similar=%d",
            sec["sec_id"], sec["name"], len(items), inserted, dup, stale, similar,
        )

    log.info(
        "done: read=%d inserted=%d duplicate=%d skipped_stale=%d skipped_similar=%d",
        total_read, total_inserted, total_dup, total_stale, total_similar,
    )


if __name__ == "__main__":
    collect()
