"""Two-tier price collector for every security on any user's watchlist.

Auto tier (securities.price_source == 'yfinance'):
  - Batch-fetches daily history from Yahoo Finance and upserts one `quotes`
    row per security: last price, previous close, day change, and period
    returns (MTD/QTD/YTD/12M).
  - Period anchors are recomputed once per calendar day (quotes.anchors_date
    gate); in-between runs only refresh last_price/prev_close/day_change.
  - ILA (agorot) trap: yfinance quotes .TA equities in ILA; prices are
    divided by 100 and stored as ILS. Percent returns are scale-invariant,
    so only stored price levels need conversion.
  - Validation: empty or all-NaN history never overwrites good data — a
    security with an existing quotes row is marked status='stale', one
    without becomes status='no_data'.

Manual tier (securities.price_source == 'manual'):
  - Reads price points from `manual_prices` (entered via
    `python -m desk.manual_price <sec_id> <YYYY-MM-DD> <close>`).
  - last_price/as_of come from the latest entry; day_change_pct is NULL;
    period returns use the nearest entry on-or-before each anchor date
    (NULL when no entry predates the anchor).

Off-hours/weekends are safe: the last daily close is simply re-reported.
TASE trades Mon-Fri as of 2026, so no special calendar gating is needed.

Raw prices only — no LLM calls, no scoring. WRITE-only against DESK_DB_URL.
"""
import logging
from bisect import bisect_right
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd
import yfinance as yf
from sqlalchemy import select

from desk.db import get_engine, init_db, manual_prices, quotes, securities, upsert, watchlist
from desk.securities import resolve_yahoo_symbol

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_prices")

PERIOD_COLS = ["mtd_pct", "qtd_pct", "ytd_pct", "y12_pct"]
# 12m anchor + nearest prior trading day + holidays: 400 calendar days is plenty.
HISTORY_DAYS = 400


def watchlisted_securities(engine) -> list[dict]:
    """Distinct securities across the UNION of all users' watchlists."""
    stmt = select(securities).join(watchlist, watchlist.c.sec_id == securities.c.sec_id).distinct()
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(stmt)]


def existing_quotes(engine) -> dict[str, dict]:
    with engine.connect() as conn:
        return {row.sec_id: dict(row._mapping) for row in conn.execute(select(quotes))}


def anchor_dates(today: date) -> dict[str, date]:
    """Per period, the calendar date whose nearest on-or-before close is the
    return baseline: the day before each period start, and ~12 months ago."""
    month_start = today.replace(day=1)
    quarter_start = today.replace(month=today.month - (today.month - 1) % 3, day=1)
    year_start = today.replace(month=1, day=1)
    return {
        "mtd_pct": month_start - timedelta(days=1),
        "qtd_pct": quarter_start - timedelta(days=1),
        "ytd_pct": year_start - timedelta(days=1),
        "y12_pct": today - timedelta(days=365),
    }


def close_on_or_before(dates: list[date], closes: list[float], anchor: date) -> float | None:
    """Latest close whose date <= anchor; None if history starts after it."""
    i = bisect_right(dates, anchor)
    return closes[i - 1] if i else None


def pct_change(current: float, base: float | None) -> float | None:
    if base is None or base == 0:
        return None
    return (current / base - 1.0) * 100.0


def period_returns(dates: list[date], closes: list[float], last: float, today: date) -> dict:
    out = {}
    for col, anchor in anchor_dates(today).items():
        out[col] = pct_change(last, close_on_or_before(dates, closes, anchor))
    return out


def closes_series(df: pd.DataFrame | None, symbol: str) -> tuple[list[date], list[float]] | None:
    """Extract (dates, closes) for one ticker from a yf.download frame.

    Returns None when the ticker has no usable data (empty / all-NaN) —
    the caller must then decide between status='no_data' and 'stale'.
    """
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        if symbol not in df.columns.get_level_values(0):
            return None
        series = df[symbol]["Close"]
    else:
        series = df["Close"]
    series = series.dropna()
    if series.empty:
        return None
    dates = [ts.date() for ts in series.index]
    return dates, [float(v) for v in series.to_list()]


def currency_for(symbol: str, cached: str | None) -> str:
    """Native quote currency from Yahoo, cached via quotes.currency.

    The stored value is always post-conversion ('ILS'), which maps back to
    native 'ILA' for .TA symbols so the /100 rule keeps applying.
    """
    if cached:
        return "ILA" if cached == "ILS" and symbol.endswith(".TA") else cached
    try:
        cur = yf.Ticker(symbol).fast_info["currency"]
        if cur:
            return cur
    except Exception as e:
        log.warning("currency lookup failed for %s (%s); falling back on suffix", symbol, e)
    return "ILA" if symbol.endswith(".TA") else "USD"


def collect_auto(engine, secs: list[dict], existing: dict[str, dict], today: date) -> None:
    if not secs:
        return
    symbols = {s["sec_id"]: resolve_yahoo_symbol(s["symbol"], s["market"], s.get("yahoo_symbol")) for s in secs}
    need_anchors = any(
        existing.get(s["sec_id"]) is None or existing[s["sec_id"]]["anchors_date"] != today for s in secs
    )
    days = HISTORY_DAYS if need_anchors else 12
    start = today - timedelta(days=days)
    log.info("auto tier: %d securities, history window %dd (anchors %s)", len(secs), days, "refresh" if need_anchors else "fresh")
    try:
        df = yf.download(
            list(symbols.values()),
            start=start.isoformat(),
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
    except Exception as e:
        log.error("batch download failed: %s", e)
        df = None

    for sec in secs:
        sec_id, symbol = sec["sec_id"], symbols[sec["sec_id"]]
        prior = existing.get(sec_id)
        data = closes_series(df, symbol)
        if data is None:
            # Never overwrite good data with junk (hard lesson from research).
            if prior is None:
                values = {"sec_id": sec_id, "source": "yfinance", "status": "no_data"}
            else:
                values = {"sec_id": sec_id, "source": "yfinance", "status": "stale"}
            log.warning("%s (%s): no usable history -> %s", sec_id, symbol, values["status"])
        else:
            dates, closes = data
            currency = currency_for(symbol, prior["currency"] if prior else None)
            scale = 0.01 if currency == "ILA" else 1.0  # ILA (agorot) -> ILS
            last, last_date = closes[-1], dates[-1]
            prev = closes[-2] if len(closes) > 1 else None
            values = {
                "sec_id": sec_id,
                "last_price": last * scale,
                "prev_close": prev * scale if prev is not None else None,
                "day_change_pct": pct_change(last, prev),
                "currency": "ILS" if currency == "ILA" else currency,
                "as_of": datetime.combine(last_date, time.min, tzinfo=timezone.utc),
                "source": "yfinance",
                "status": "ok",
            }
            if need_anchors and (prior is None or prior["anchors_date"] != today):
                returns = period_returns(dates, closes, last, today)
                values.update(returns)
                values["anchors_date"] = today
                missing = [c for c, v in returns.items() if v is None]
                if missing:
                    log.info("%s (%s): history since %s -> %s = NULL", sec_id, symbol, dates[0], ",".join(missing))
            log.info("%s (%s): last=%.2f %s as_of=%s", sec_id, symbol, values["last_price"], values["currency"], last_date)

        with engine.begin() as conn:
            conn.execute(upsert(engine, quotes, ["sec_id"], values))


def collect_manual(engine, secs: list[dict], today: date) -> None:
    for sec in secs:
        sec_id = sec["sec_id"]
        stmt = (
            select(manual_prices.c.price_date, manual_prices.c.close)
            .where(manual_prices.c.sec_id == sec_id)
            .order_by(manual_prices.c.price_date)
        )
        with engine.connect() as conn:
            rows = conn.execute(stmt).all()
        if not rows:
            values = {"sec_id": sec_id, "source": "manual", "status": "no_data"}
            log.info("%s (%s): no manual prices -> no_data", sec_id, sec["symbol"])
        else:
            dates = [r.price_date for r in rows]
            closes = [float(r.close) for r in rows]
            last, last_date = closes[-1], dates[-1]
            values = {
                "sec_id": sec_id,
                "last_price": last,
                "prev_close": None,
                "day_change_pct": None,  # manual entries are sparse; no meaningful daily change
                "currency": "ILS" if sec["market"] == "TASE" else "USD",
                "as_of": datetime.combine(last_date, time.min, tzinfo=timezone.utc),
                "anchors_date": today,
                "source": "manual",
                "status": "ok",
                **period_returns(dates, closes, last, today),
            }
            log.info("%s (%s): last=%.2f %s as_of=%s (manual)", sec_id, sec["symbol"], last, values["currency"], last_date)
        with engine.begin() as conn:
            conn.execute(upsert(engine, quotes, ["sec_id"], values))


def collect() -> None:
    engine = get_engine()
    init_db(engine)
    secs = watchlisted_securities(engine)
    auto = [s for s in secs if s["price_source"] == "yfinance"]
    manual = [s for s in secs if s["price_source"] == "manual"]
    other = [s for s in secs if s["price_source"] not in ("yfinance", "manual")]
    for s in other:
        log.warning("%s: unknown price_source %r, skipped", s["sec_id"], s["price_source"])
    today = datetime.now(timezone.utc).date()
    collect_auto(engine, auto, existing_quotes(engine), today)
    collect_manual(engine, manual, today)
    log.info("done: auto=%d manual=%d skipped=%d", len(auto), len(manual), len(other))


if __name__ == "__main__":
    collect()
