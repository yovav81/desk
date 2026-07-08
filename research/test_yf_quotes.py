"""Phase 0: yfinance test - TA stocks, US stocks: last price, MTD, YTD, currency."""
import json
import yfinance as yf

TICKERS = ["TEVA.TA", "LUMI.TA", "POLI.TA", "AAPL", "MSFT"]

results = {}
for t in TICKERS:
    try:
        tk = yf.Ticker(t)
        hist = tk.history(period="ytd", auto_adjust=True)
        if hist.empty:
            results[t] = {"error": "empty history"}
            continue
        last = hist["Close"].iloc[-1]
        first_ytd = hist["Close"].iloc[0]
        # MTD: first close of current month
        this_month = hist[hist.index.month == hist.index[-1].month]
        this_month = this_month[this_month.index.year == hist.index[-1].year]
        first_mtd = this_month["Close"].iloc[0]
        fi = tk.fast_info
        results[t] = {
            "last_close": round(float(last), 2),
            "last_date": str(hist.index[-1].date()),
            "ytd_pct": round((last / first_ytd - 1) * 100, 2),
            "mtd_pct": round((last / first_mtd - 1) * 100, 2),
            "rows_ytd": len(hist),
            "currency": getattr(fi, "currency", None),
            "exchange": getattr(fi, "exchange", None),
            "fast_last_price": round(float(fi.last_price), 2) if fi.last_price else None,
        }
    except Exception as e:
        results[t] = {"error": f"{type(e).__name__}: {e}"}

print(json.dumps(results, indent=2, ensure_ascii=False))
