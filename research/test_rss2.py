"""Phase 0 round 2: verify Hebrew encoding properly, retry calcalist w/ browser UA, bizportal candidates."""
import json
import urllib.request
import xml.etree.ElementTree as ET

FEEDS = {
    "globes_market_heb": "https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=2",
    "calcalist_main": "https://www.calcalist.co.il/GeneralRSS/0,16335,L-8,00.xml",
    "calcalist_markets": "https://www.calcalist.co.il/GeneralRSS/0,16335,L-14,00.xml",
    "bizportal_a": "https://www.bizportal.co.il/rss/news",
    "bizportal_b": "https://www.bizportal.co.il/mobile/rss/GetRss?category=news",
    "bizportal_c": "https://www.bizportal.co.il/feed/rss",
    "themarker": "https://www.themarker.com/cmlink/1.144",
}

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "application/rss+xml, application/xml, text/xml, */*"}
out = {}
for name, url in FEEDS.items():
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
        root = ET.fromstring(raw)
        items = root.findall(".//item")
        title = items[0].findtext("title", "") if items else ""
        has_hebrew = any("֐" <= ch <= "ת" for ch in title)
        out[name] = {"ok": True, "items": len(items), "hebrew_ok": has_hebrew,
                     "title_repr": title[:60]}
    except Exception as e:
        out[name] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:100]}"}

with open("rss2_out.json", "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2, ensure_ascii=True)
print(json.dumps(out, indent=2, ensure_ascii=True))
