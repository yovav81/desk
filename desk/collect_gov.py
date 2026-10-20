"""Official-source macro collector: Bank of Israel press releases (Phase 28).

Why a browser: boi.org.il answers plain HTTP clients with a Radware JS-challenge
loader ("Radware Page", no release list — MEASURED from a GitHub runner,
2026-10-20), while headless Chromium clears it and renders the list (16 links,
dated, MEASURED the same day). There is no RSS/API for the press releases, and
Google News `site:boi.org.il` returns tenders/old pages, not releases.

Rows land in `news` exactly like collect_macro's: sec_id NULL, category 'macro',
source 'boi' -> the macro tab picks them up with no UI change. Headline + link
only (never bodies), UNIQUE(url) is the only dedup — an official release is not
suppressed by the near-duplicate gate, even if a newspaper covers the same event.

published_at: the list shows a DATE only. Today's releases get the time we first
saw them (a few minutes after publication — the collector runs every ~30 min);
earlier dates get Israel midnight. Never an invented clock time for an old item.

Runs inside filings.yml (Chromium is already installed there), gated to minutes
0-5 of each half hour (`GOV_FORCE=1` overrides). Fail-soft end to end: a blocked
page logs "FEED SILENT" and exits 0 — it must never turn the filings lane red.
"""
import logging
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from desk.collect_news import is_stale
from desk.db import get_engine, init_db, insert_ignore, news

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_gov")

IL = ZoneInfo("Asia/Jerusalem")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

BOI_LIST_URL = "https://boi.org.il/publications/pressreleases/"
# A release link is /pressreleases/<slug>/ (slug non-empty); the bare listing and
# the language switchers (/ar/...) share the substring but have no slug.
_REL_HREF = re.compile(r"^https?://(?:www\.)?boi\.org\.il/publications/pressreleases/([^/?#]+)/?$")
_DATE_LINE = re.compile(r"^\s*(\d{2})/(\d{2})/(\d{4})\s*$")


def gov_active(now: datetime) -> bool:
    """Half-hourly gate (same idea as GDELT's hourly one). filings.yml starts
    every ~5 min, so minutes 0-5 of :00/:30 catch a run; GOV_FORCE=1 overrides."""
    if os.environ.get("GOV_FORCE") == "1":
        return True
    return now.minute % 30 < 6


def parse_boi_links(links: list[tuple[str, str]], now: datetime) -> list[dict]:
    """[(href, innerText)] -> [{title, url, published_at}]. PURE (unit-testable).

    innerText of a release link is "DD/MM/YYYY\\n<title>". Anything that does not
    match both the href shape and the date-first text shape is dropped — a
    structural change on their side yields read=0 (loud), never garbage rows.
    """
    out, seen = [], set()
    today_il = now.astimezone(IL).date()
    for href, text in links:
        if not _REL_HREF.match(href or "") or href in seen:
            continue
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        m = _DATE_LINE.match(lines[0])
        if not m:
            continue
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            day = datetime(y, mo, d, tzinfo=IL)
        except ValueError:
            continue
        title = " ".join(lines[1:])
        published_at = now if day.date() == today_il else day.astimezone(timezone.utc)
        seen.add(href)
        out.append({"title": title, "url": href, "published_at": published_at})
    return out


def fetch_boi_links(timeout_ms: int = 45000) -> list[tuple[str, str]]:
    """Render the BoI press-release list in headless Chromium; [] on any failure."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        log.warning("playwright unavailable (%s) — cannot read BoI", e)
        return []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--disable-blink-features=AutomationControlled"]
            )
            ctx = browser.new_context(
                user_agent=UA, locale="he-IL",
                extra_http_headers={"Accept-Language": "he-IL,he;q=0.9,en;q=0.8"},
                viewport={"width": 1366, "height": 900},
            )
            ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            page = ctx.new_page()
            # NOT networkidle: it never settled on this site (45s timeout, measured)
            # although the list was already rendered. Wait for the links instead.
            page.goto(BOI_LIST_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_function(
                    "document.querySelectorAll(\"a[href*='/pressreleases/']\").length > 6",
                    timeout=40000,
                )
            except Exception as e:
                log.info("BoI link wait timed out (reading what rendered): %s", e)
            links = page.eval_on_selector_all(
                "a[href*='/pressreleases/']",
                "els => els.map(e => [e.href, e.innerText])",
            )
            browser.close()
            return [(h, t) for h, t in links]
    except Exception as e:
        log.warning("BoI fetch crashed: %s", e)
        return []


def collect() -> None:
    now = datetime.now(timezone.utc)
    if not gov_active(now):
        log.info("GOV gated off this run (minute %d)", now.minute)
        return

    items = parse_boi_links(fetch_boi_links(), now)
    if not items:
        # A dead/blocked source must scream (house rule) — but never fail the lane.
        log.warning("GOV boi read=0 — FEED SILENT")
        return

    engine = get_engine()
    init_db(engine)
    inserted = stale = attempted = 0
    with engine.begin() as conn:
        for it in items:
            if is_stale(it["published_at"], now):
                stale += 1
                continue
            attempted += 1
            stmt = insert_ignore(engine, news, ["url"]).values(
                sec_id=None, source="boi", title=it["title"], url=it["url"],
                published_at=it["published_at"], summary=None, category="macro",
            ).returning(news.c.id)
            # inserted = rows the DB RETURNED (rowcount lies on ON CONFLICT DO NOTHING).
            if conn.execute(stmt).first() is not None:
                inserted += 1
    log.info("GOV boi read=%d inserted=%d duplicate=%d skipped_stale=%d",
             len(items), inserted, attempted - inserted, stale)


if __name__ == "__main__":
    try:
        collect()
    except Exception as e:  # fail-soft: never red the filings lane over this
        log.warning("GOV collector crashed (ignored): %s", e)
