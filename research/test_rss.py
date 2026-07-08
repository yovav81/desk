"""Phase 0: test candidate RSS feeds - status, item count, Hebrew encoding, fields."""
import json
import urllib.request
import xml.etree.ElementTree as ET

FEEDS = {
    "globes_main": "https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=1725",
    "globes_market": "https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=2",
    "calcalist_main": "https://www.calcalist.co.il/GeneralRSS/0,16335,L-8,00.xml",
    "calcalist_markets": "https://www.calcalist.co.il/GeneralRSS/0,16335,L-14,00.xml",
    "bizportal": "https://www.bizportal.co.il/mobile/rss/mainpage",
    "bizportal2": "https://rss.bizportal.co.il/rss/news.xml",
    "themarker": "https://www.themarker.com/srv/tm-articles-rss",
    "themarker2": "https://www.themarker.com/cmlink/1.144",
    "googlenews_teva": "https://news.google.com/rss/search?q=%22%D7%98%D7%91%D7%A2+%D7%AA%D7%A2%D7%A9%D7%99%D7%95%D7%AA%22&hl=he&gl=IL&ceid=IL:he",
}

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeskResearch/0.1"}
out = {}
for name, url in FEEDS.items():
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
        root = ET.fromstring(raw)
        items = root.findall(".//item")
        first = items[0] if items else None
        fields = sorted({child.tag for child in first}) if first is not None else []
        title = first.findtext("title", "") if first is not None else ""
        pub = first.findtext("pubDate", "") if first is not None else ""
        out[name] = {
            "ok": True, "items": len(items), "fields": fields,
            "sample_title": title[:80], "sample_pubdate": pub,
        }
    except Exception as e:
        out[name] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}

print(json.dumps(out, indent=2, ensure_ascii=False))
