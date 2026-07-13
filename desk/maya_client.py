"""Shared MAYA (maya.tase.co.il) access helpers for DESK collectors.

MAYA sits behind an Imperva/Incapsula bot gate with no login. The proven
pattern (see research/MAYA_FINDINGS.md and the 2b Actions probe) is:
  1. load the site once in headless Chromium to clear the gate and collect
     cookies (realistic desktop UA, he-IL, automation flags masked),
  2. replay those cookies in a plain requests.Session for the JSON API.

Gotchas carried over from the pre-check:
  - NEVER send `Content-Type: application/json` on a GET — the WAF returns 403.
    Set it only on POSTs.
  - Documents live at https://mayafiles.tase.co.il/ + attachment path.
  - Human-readable report page: https://maya.tase.co.il/reports/details/<id>.

This module reads no secrets and writes nothing; it is pattern-replicated,
never linked to any other project.
"""
import logging
import time

import requests

log = logging.getLogger("maya")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
BASE = "https://maya.tase.co.il/"
SEARCH_URL = "https://apicontent.tase.co.il/api/search/market"
AUTOCOMPLETE_URL = "https://maya.tase.co.il/api/v1/companies/autocomplete"
REPORTS_URL = "https://maya.tase.co.il/api/v1/reports/companies"
DOC_BASE = "https://mayafiles.tase.co.il/"
DETAIL_BASE = "https://maya.tase.co.il/reports/details/"


def harvest_cookies(timeout_ms: int = 60000) -> list[dict]:
    """Load MAYA in headless Chromium to clear the bot gate; return cookies.

    Returns [] if Playwright/Chromium is unavailable or the load crashes — the
    caller decides how to fail-soft. An empty return or one lacking Incapsula
    cookies means the gate was not cleared.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # playwright not installed (e.g. local dev)
        log.warning("playwright unavailable (%s) — cannot harvest MAYA cookies", e)
        return []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                user_agent=UA,
                locale="he-IL",
                extra_http_headers={"Accept-Language": "he-IL,he;q=0.9,en;q=0.8"},
                viewport={"width": 1366, "height": 900},
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = ctx.new_page()
            page.goto(BASE, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception as e:
                log.info("MAYA networkidle wait timed out (continuing): %s", e)
            time.sleep(3)
            cookies = ctx.cookies()
            browser.close()
            return cookies
    except Exception as e:
        log.warning("MAYA cookie harvest crashed: %s", e)
        return []


def gate_cleared(cookies: list[dict]) -> bool:
    """True when the Incapsula session cookies that mark a cleared gate exist."""
    names = [c.get("name", "") for c in cookies]
    return any(n.startswith(("visid_incap", "incap_ses")) for n in names)


def make_session(cookies: list[dict]) -> requests.Session:
    """A requests.Session carrying harvested cookies + matching browser headers.

    Note: no Content-Type here on purpose (GET trap). POST callers add
    `Content-Type: application/json` per-request.
    """
    s = requests.Session()
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
        "Referer": BASE,
    })
    return s


def doc_url_from_attachments(attachments: list[dict] | None) -> str | None:
    """Full URL of the first attachment, or None. mayafiles base + relative path."""
    if not attachments:
        return None
    rel = attachments[0].get("url")
    return DOC_BASE + rel if rel else None
