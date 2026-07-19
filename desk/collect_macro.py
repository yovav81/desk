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
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import select

# ONE staleness definition for the whole news table — see the rationale at its
# definition (Google News archive noise). Globes is a curated latest-N feed and
# probably never trips it, but that is an assumption; the skipped_stale counter
# measures it instead of trusting it.
from desk.collect_news import SIMILAR_HOURS, fetch_gdelt, is_similar, is_stale, norm_tokens
from desk.db import get_engine, init_db, insert_ignore, news

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_macro")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeskCollector/0.1"

# (source_label, url). Globes iID 2 = home page / general economy (alive).
# globes_markets (iID=585) went silent 2026-07-14 and is retired — replaced by
# Ynet's economy RSS. Both Hebrew UTF-8; pubDate is RFC822 with an explicit
# offset, parsed tz-aware by fetch_feed. Add more feeds by extending this list.
GLOBES_RSS = "https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID={iid}"
MACRO_FEEDS = [
    ("globes_home", GLOBES_RSS.format(iid=2)),
    ("ynet_economy", "https://www.ynet.co.il/Integration/StoryRss6.xml"),
]
# World-macro lane (Phase 12C): GDELT keyless DOC API, English press, 1 day.
# Rides the same loop as the RSS feeds (same gates/log); url=None routes the
# fetch to fetch_gdelt. Its failure warns + continues like any dead feed.
GDELT_MACRO_QUERY = '("interest rate" OR "central bank" OR inflation OR "stock market") sourcelang:english'


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

    now = datetime.now(timezone.utc)
    # Near-dup group (Phase 12D): ONE query per run — last SIMILAR_HOURS of
    # macro titles (sec_id NULL). Shared across feeds, so the same story on
    # Globes AND Ynet is caught; inserted titles join it for intra-run dupes.
    with engine.connect() as conn:
        known = [
            norm_tokens(t) for (t,) in conn.execute(
                select(news.c.title).where(
                    news.c.sec_id.is_(None),
                    news.c.published_at >= now - timedelta(hours=SIMILAR_HOURS),
                )
            )
        ]
    total_read = total_inserted = total_dup = total_stale = total_similar = 0
    for source, url in MACRO_FEEDS + [("gdelt_macro", None)]:
        try:
            items = fetch_gdelt(GDELT_MACRO_QUERY, "1d", 30) if url is None else fetch_feed(url)
        except Exception as e:
            log.warning("macro feed failed for %s (%s): %s", source, url, e)
            continue

        inserted = stale = similar = 0
        with engine.begin() as conn:
            for it in items:
                if is_stale(it["published_at"], now):
                    stale += 1
                    continue
                toks = norm_tokens(it["title"])
                if any(is_similar(toks, t) for t in known):
                    similar += 1
                    continue
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
                    inserted += 1
                    known.append(toks)

        dup = len(items) - inserted - stale - similar
        total_read += len(items)
        total_inserted += inserted
        total_dup += dup
        total_stale += stale
        total_similar += similar
        # read=0 must scream: a live feed returning nothing is how globes_markets
        # died silently for days.
        if len(items) == 0:
            log.warning("MACRO %s read=0 — FEED SILENT", source)
        else:
            log.info(
                "MACRO %s read=%d inserted=%d duplicate=%d skipped_stale=%d skipped_similar=%d",
                source, len(items), inserted, dup, stale, similar,
            )

    log.info(
        "done: read=%d inserted=%d duplicate=%d skipped_stale=%d skipped_similar=%d",
        total_read, total_inserted, total_dup, total_stale, total_similar,
    )


if __name__ == "__main__":
    collect()
