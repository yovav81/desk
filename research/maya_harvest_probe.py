"""TEMPORARY Phase 2b go/no-go probe — delete after the 2b decision.

Self-contained: proves the MAYA Imperva/Incapsula cookie-harvest works from
wherever this runs (locally vs a GitHub Actions datacenter IP), then exercises
the full announcement path end-to-end. Prints a single PASS/FAIL line and exits
0 only on PASS. No secrets, no DB, no DESK_DB_URL.

Path proven in the 2b pre-check (research/MAYA_FINDINGS.md):
  1. headless Chromium harvests cookies past the bot gate
  2. replay cookies in requests to GET the site-wide breaking feed
  3. resolve ONE companyId via the 2-hop search (number -> name -> id)
  4. POST the per-company feed and confirm real titles + dates come back

Gotcha carried over: never send Content-Type: application/json on a GET (WAF 403);
set it only on the POST.
"""
import json
import sys
import time

import requests
from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
BASE = "https://maya.tase.co.il/"
BREAKING = "https://maya.tase.co.il/api/v1/reports/breaking-announcement?limit=5"
SEARCH = "https://apicontent.tase.co.il/api/search/market"
REPORTS = "https://maya.tase.co.il/api/v1/reports/companies"
TEVA_SECNO = "629014"


def harvest_cookies():
    """Return (cookies, title) from a headless Chromium load past the gate."""
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
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception as e:
            print(f"[harvest] networkidle wait timed out (continuing): {e}")
        time.sleep(3)
        title = page.title()
        cookies = ctx.cookies()
        browser.close()
        return cookies, title


def make_session(cookies):
    s = requests.Session()
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
        "Referer": "https://maya.tase.co.il/",
    })
    return s


def get_json(s, url):
    r = s.get(url, timeout=30)
    ct = r.headers.get("content-type", "")
    if r.status_code == 200 and "json" in ct.lower():
        try:
            return r.json(), r.status_code
        except Exception:
            return None, r.status_code
    return None, r.status_code


def resolve_teva_company_id(s):
    """2-hop: security number -> official name -> companyId."""
    r1 = s.get(SEARCH, params={"q": TEVA_SECNO, "culture": "he-IL"}, timeout=30)
    if r1.status_code != 200:
        return None, f"search-by-number HTTP {r1.status_code}"
    rows1 = (r1.json() or {}).get("data") or []
    name = next((row.get("name") for row in rows1 if row.get("id") == TEVA_SECNO), None)
    if not name:
        return None, "official name not found in number search"
    r2 = s.get(SEARCH, params={"q": name, "culture": "he-IL"}, timeout=30)
    if r2.status_code != 200:
        return None, f"search-by-name HTTP {r2.status_code}"
    rows2 = (r2.json() or {}).get("data") or []
    for row in rows2:
        if "/companies/" in (row.get("url") or ""):
            return row.get("id"), name
    return None, f"companyId not found for name {name!r}"


def fetch_company_reports(s, company_id):
    body = {"pageNumber": 1, "companyId": int(company_id), "limit": 5, "offset": 0}
    r = s.post(
        REPORTS,
        data=json.dumps(body),
        timeout=30,
        headers={"Content-Type": "application/json", "Origin": "https://maya.tase.co.il"},
    )
    if r.status_code != 200:
        return None, r.status_code
    try:
        data = r.json()
    except Exception:
        return None, r.status_code
    rows = data if isinstance(data, list) else data.get("reports") or data.get("data") or []
    return rows, r.status_code


def main():
    print("=== MAYA harvest probe (TEMPORARY 2b go/no-go) ===")

    # a. Harvest
    try:
        cookies, title = harvest_cookies()
    except Exception as e:
        print(f"FAIL: cookie harvest crashed: {e}")
        return 1
    cookie_names = [c["name"] for c in cookies]
    gate_pass = any(n.startswith(("visid_incap", "incap_ses")) for n in cookie_names)
    print(f"[a] gate: {len(cookies)} cookies, title={title!r}")
    print(f"[a] incapsula cookies present: {gate_pass}")
    if not cookies:
        print("FAIL: no cookies harvested — gate almost certainly blocked us")
        return 1

    s = make_session(cookies)

    # b. Site-wide breaking feed
    breaking, status_b = get_json(s, BREAKING)
    ok_b = isinstance(breaking, list) and len(breaking) > 0 and bool(breaking[0].get("title"))
    print(f"[b] breaking-announcement: HTTP {status_b}, items={len(breaking) if isinstance(breaking, list) else 'n/a'}, usable={ok_b}")
    if ok_b:
        print(f"      e.g. [{breaking[0].get('publishDate')}] {breaking[0].get('title')}")

    # c. Resolve Teva + per-company feed
    company_id, info = resolve_teva_company_id(s)
    print(f"[c] resolve Teva {TEVA_SECNO} -> companyId {company_id} ({info})")
    ok_c = False
    if company_id:
        rows, status_c = fetch_company_reports(s, company_id)
        ok_c = isinstance(rows, list) and len(rows) > 0 and bool(rows[0].get("title")) and bool(rows[0].get("publishDate"))
        print(f"[c] reports/companies: HTTP {status_c}, items={len(rows) if isinstance(rows, list) else 'n/a'}, usable={ok_c}")
        if ok_c:
            for row in rows[:3]:
                print(f"      [{row.get('publishDate')}] {row.get('title')}")

    # d. Verdict: PASS only if gate passed AND real announcement JSON came back
    passed = bool(cookies) and ok_b and ok_c
    print()
    if passed:
        print("RESULT: PASS — gate cleared and live announcement JSON (titles+dates) returned")
        return 0
    print("RESULT: FAIL — see statuses above (blocked/challenge or empty JSON)")
    if not gate_pass:
        print("  hint: no Incapsula cookies -> likely served a bot challenge from this IP")
    return 1


if __name__ == "__main__":
    sys.exit(main())
