"""Collect news for every security on any user's watchlist via Google News RSS.

Raw data only: no LLM calls, no summarization. `summary` is always left NULL.
Dedup guard: news.url is UNIQUE; INSERT ... ON CONFLICT(url) DO NOTHING.

For TASE securities, results are best when data/securities.csv's `name`
column is in Hebrew (Google News hl/gl is set to he/IL for market=TASE).
"""
import logging
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from sqlalchemy import select

from desk.db import get_engine, init_db, insert_ignore, news, securities, watchlist

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_news")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeskCollector/0.1"


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


def collect() -> None:
    engine = get_engine()
    init_db(engine)
    secs = watchlisted_securities(engine)
    log.info("securities on watchlists: %d", len(secs))

    total_fetched = total_new = total_skipped = 0
    for sec in secs:
        url = rss_url_for(sec)
        try:
            items = fetch_feed(url)
        except Exception as e:
            log.warning("feed failed for %s (%s): %s", sec["sec_id"], sec["name"], e)
            continue

        new_count = 0
        with engine.begin() as conn:
            for it in items:
                stmt = insert_ignore(engine, news, ["url"]).values(
                    sec_id=sec["sec_id"],
                    source="google_news",
                    title=it["title"],
                    url=it["url"],
                    published_at=it["published_at"],
                    summary=None,
                )
                result = conn.execute(stmt)
                if result.rowcount:
                    new_count += 1

        total_fetched += len(items)
        total_new += new_count
        total_skipped += len(items) - new_count
        log.info("%s (%s): fetched=%d new=%d skipped=%d", sec["sec_id"], sec["name"], len(items), new_count, len(items) - new_count)

    log.info("done: fetched=%d new=%d skipped=%d", total_fetched, total_new, total_skipped)


if __name__ == "__main__":
    collect()
