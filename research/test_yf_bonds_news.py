"""Phase 0: (a) yfinance for TASE bonds by security number, (b) yfinance news fields for AAPL."""
import json
import yfinance as yf

# TASE bond security numbers as .TA tickers (numeric-form guesses + known example)
BOND_TICKERS = ["1234673.TA", "1166107.TA", "5760475.TA"]

out = {"bonds": {}, "search": {}, "news_fields": None, "news_sample": None}
for t in BOND_TICKERS:
    try:
        h = yf.Ticker(t).history(period="5d")
        out["bonds"][t] = {"rows": len(h), "last": float(h["Close"].iloc[-1]) if len(h) else None}
    except Exception as e:
        out["bonds"][t] = {"error": f"{type(e).__name__}: {str(e)[:100]}"}

# Does Yahoo even list TASE bonds? Search for a govt bond name.
try:
    s = yf.Search("ILGOV", max_results=8)
    out["search"]["ILGOV"] = [(q.get("symbol"), q.get("exchange"), q.get("quoteType")) for q in s.quotes]
except Exception as e:
    out["search"]["ILGOV"] = f"{type(e).__name__}: {str(e)[:100]}"

try:
    news = yf.Ticker("AAPL").news
    if news:
        n0 = news[0]
        out["news_fields"] = sorted(n0.keys())
        if "content" in n0:
            out["news_content_fields"] = sorted(n0["content"].keys())
            c = n0["content"]
            out["news_sample"] = {
                "title": c.get("title", "")[:80],
                "pubDate": c.get("pubDate"),
                "provider": (c.get("provider") or {}).get("displayName"),
            }
        out["news_count"] = len(news)
except Exception as e:
    out["news_error"] = f"{type(e).__name__}: {str(e)[:100]}"

print(json.dumps(out, indent=2, ensure_ascii=False))
