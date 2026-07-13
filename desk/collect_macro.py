"""Collect general Israeli-economy / market-review headlines NOT tied to any
watchlist security. Cloud collector, WRITE-only against DESK_DB_URL — runs on
the same 15-min schedule as the other collectors.

Stores into `news` with category='macro', sec_id=NULL. Dedup guard: news.url
is UNIQUE (INSERT ... ON CONFLICT(url) DO NOTHING) — safe to re-run on a cron.
Raw data only: no LLM calls, summary stays NULL.

Sources are Globes RSS section feeds (verified alive, clean Hebrew UTF-8 in
Phase 0). Calcalist and Bizportal block direct RSS (WAF/Cloudflare 403 per
Phase 0) — not fought here; their stories still arrive per-security via the
Google News collector. Add more feeds by extending MACRO_FEEDS.
"""
import logging
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from sqlalchemy import select

from desk.db import get_engine, init_db, insert_ignore, news

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_macro")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeskCollector/0.1"

# (source_label, url). Globes iID sections: 2 = home page / general economy,
# 585 = capital markets & investments. Both Hebrew, verified alive.
GLOBES_RSS = "https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID={iid}"
MACRO_FEEDS = [
    ("globes_home", GLOBES_RSS.format(iid=2)),
    ("globes_markets", GLOBES_RSS.format(iid=585)),
]


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

    total_fetched = total_new = total_skipped = 0
    for source, url in MACRO_FEEDS:
        try:
            items = fetch_feed(url)
        except Exception as e:
            log.warning("macro feed failed for %s (%s): %s", source, url, e)
            continue

        new_count = 0
        with engine.begin() as conn:
            for it in items:
                stmt = insert_ignore(engine, news, ["url"]).values(
                    sec_id=None,
                    source=source,
                    title=it["title"],
                    url=it["url"],
                    published_at=it["published_at"],
                    summary=None,
                    category="macro",
                )
                if conn.execute(stmt).rowcount:
                    new_count += 1

        total_fetched += len(items)
        total_new += new_count
        total_skipped += len(items) - new_count
        log.info("%s: fetched=%d new=%d skipped=%d", source, len(items), new_count, len(items) - new_count)

    log.info("done: fetched=%d new=%d skipped=%d", total_fetched, total_new, total_skipped)


if __name__ == "__main__":
    collect()
