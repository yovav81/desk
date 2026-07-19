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
import time
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


def fetch_gdelt(query: str, timespan: str, maxrecords: int) -> list[dict]:
    """GDELT 2.1 DOC API (keyless). seendate is YYYYMMDDTHHMMSSZ, real UTC.
    MEASURED 2026-07-19: GDELT rate-limits per IP with a cooldown — burst
    calls got 429 regardless of UA; the same query returned 200 after ~90s
    quiet. Callers space calls (sleep) and treat 429 as skip, never retry."""
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={quote(query)}&mode=artlist&format=json"
        f"&maxrecords={maxrecords}&timespan={timespan}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:  # 20s cap — a stall can't eat the step
        data = json.loads(r.read())
    items = []
    for a in data.get("articles", []):
        title, link, seen = a.get("title"), a.get("url"), a.get("seendate")
        published_at = None
        if seen:
            try:
                published_at = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        if title and link:
            items.append({"title": title, "url": link, "published_at": published_at})
    return items


# 12C-FIX: one call per security starved the 15m step (~50 GLOBAL securities x
# (call + 1s sleep + multi-minute 429 cooldowns)). Batched ORed queries cut
# ~50 calls to ~9; a consecutive-429 circuit breaker stops burning budget on a
# rate-limited IP mid-run.
GDELT_BATCH = 6
GDELT_BREAKER = 3


def _gdelt_needles(name: str) -> set[str]:
    """The 12C relevance guard's needle set (logic unchanged)."""
    return {t for t in norm_tokens(name or "") if len(t) >= 3}


def attribute_gdelt_batch(batch: list[dict], arts: list[dict]) -> tuple[dict, int]:
    """Attribute one batch's articles per security via the relevance guard.
    Multi-match goes to ALL passing securities (rare, legitimate). Returns
    ({sec_id: [items]}, offtopic_count)."""
    per = {s["sec_id"]: [] for s in batch}
    offtopic = 0
    for it in arts:
        toks = norm_tokens(it["title"])
        hits = [s for s in batch if _gdelt_needles(s["name"]) <= toks]
        for s in hits:
            per[s["sec_id"]].append(it)
        offtopic += 0 if hits else 1
    return per, offtopic


def prefetch_gdelt(global_secs: list[dict]) -> tuple[dict, int]:
    """ONE GDELT call per GDELT_BATCH securities. 3 consecutive 429s open the
    circuit — remaining batches skipped this run; any success resets the
    counter. Securities absent from the result = their batch wasn't fetched."""
    items_by_sec, offtopic, streak = {}, 0, 0
    for bi in range(0, len(global_secs), GDELT_BATCH):
        batch = global_secs[bi:bi + GDELT_BATCH]
        if bi:
            time.sleep(1)  # politeness — between batch calls only
        names = " OR ".join(f'"{s["name"]}"' for s in batch)
        try:
            arts = fetch_gdelt(f"({names}) sourcelang:english", "3d", 75)
            streak = 0
        except urllib.error.HTTPError as e:
            log.warning("gdelt HTTP %d for batch of %d — skipped", e.code, len(batch))
            if e.code == 429:
                streak += 1
                if streak >= GDELT_BREAKER:
                    log.warning(
                        "GDELT circuit open — skipping remaining %d GLOBAL securities this run",
                        len(global_secs) - bi - len(batch),
                    )
                    break
            continue
        except Exception as e:
            log.warning("gdelt batch failed: %s", e)
            continue
        per, un = attribute_gdelt_batch(batch, arts)
        items_by_sec.update(per)
        offtopic += un
        log.info("GDELT batch: read=%d attributed=%d skipped_offtopic=%d", len(arts), len(arts) - un, un)
    return items_by_sec, offtopic


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
    gdelt_items, gdelt_offtopic = prefetch_gdelt([s for s in secs if s["market"] == "GLOBAL"])
    total_read = total_inserted = total_dup = total_stale = total_similar = 0
    total_offtopic = gdelt_offtopic  # counted per batch, not per security
    for sec in secs:
        use_finnhub = sec["market"] == "US" and bool(finnhub_key)
        use_gdelt = sec["market"] == "GLOBAL"
        src = "finnhub" if use_finnhub else ("gdelt" if use_gdelt else "google_news")
        try:
            if use_finnhub:
                items = fetch_finnhub(sec["symbol"], finnhub_key, now)
            elif use_gdelt:
                if sec["sec_id"] not in gdelt_items:
                    continue  # batch 429/failed or circuit open — already logged
                items = gdelt_items[sec["sec_id"]]  # pre-attributed, guard applied
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
        # GDELT relevance filtering already happened at the batch stage
        # (attribute_gdelt_batch); per-security offtopic stays 0 by design.
        group = groups.setdefault(sec["sec_id"], [])
        inserted = stale = similar = offtopic = 0
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
                    source=src,
                    title=it["title"],
                    url=it["url"],
                    published_at=it["published_at"],
                    summary=None,
                )
                result = conn.execute(stmt)
                if result.rowcount:
                    inserted += 1
                    group.append(toks)

        dup = len(items) - inserted - stale - similar - offtopic
        total_read += len(items)
        total_inserted += inserted
        total_dup += dup
        total_stale += stale
        total_similar += similar
        total_offtopic += offtopic
        log.info(
            "%s (%s): read=%d inserted=%d duplicate=%d skipped_stale=%d skipped_similar=%d skipped_offtopic=%d",
            sec["sec_id"], sec["name"], len(items), inserted, dup, stale, similar, offtopic,
        )

    log.info(
        "done: read=%d inserted=%d duplicate=%d skipped_stale=%d skipped_similar=%d skipped_offtopic=%d",
        total_read, total_inserted, total_dup, total_stale, total_similar, total_offtopic,
    )


if __name__ == "__main__":
    collect()
