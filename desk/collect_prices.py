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

Price history (both tiers):
  - The daily anchor refresh already pulls ~400 days; the last ~1 year of that
    SAME frame is upserted into `price_history` (no extra yfinance calls) for
    the detail-page chart. Manual securities mirror their manual_prices points.
  - Stored closes are NORMALIZED (post ÷100 for agorot/pence) — identical to
    what the watchlist shows. Never raw sub-units.
  - Retention ~13 months; older rows are pruned each run.

Off-hours/weekends are safe: the last daily close is simply re-reported.
TASE trades Mon-Fri as of 2026, so no special calendar gating is needed.

Raw prices only — no LLM calls, no scoring. WRITE-only against DESK_DB_URL.
"""
import logging
import math
from bisect import bisect_right
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd
import yfinance as yf
from sqlalchemy import select

from desk.db import (
    get_engine,
    init_db,
    manual_prices,
    price_history,
    quotes,
    securities,
    upsert,
    upsert_many,
    watchlist,
)
from desk.securities import resolve_yahoo_symbol

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_prices")

PERIOD_COLS = ["mtd_pct", "qtd_pct", "ytd_pct", "y12_pct"]
# 12m anchor + nearest prior trading day + holidays: 400 calendar days is plenty.
HISTORY_DAYS = 400
# How much of the fetched series to persist for the chart (~1 year of closes).
CHART_DAYS = 365
# Retention: ~13 months, a little slack past CHART_DAYS so a 12-month chart
# never runs short at the left edge. Older rows are pruned each run.
RETENTION_DAYS = 400


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


# Yahoo occasionally switches a TASE small cap's quote unit mid-series (shekels
# <-> agorot on one date, e.g. NXSN 2026-05-18: adjacent ratio 102.6). A uniform
# ÷100 keeps the current price right but leaves pre-jump anchors ~100x off, so
# period_returns (a ratio across the jump) explodes. Rescale the earlier segment
# to the later unit BEFORE returns/history consume the array. Real splits
# (2:1..20:1) stay outside the [0.02, 50] band and are untouched.
UNIT_JUMP_HI = 50.0
UNIT_JUMP_LO = 0.02


def normalize_unit_jumps(dates: list[date], closes: list[float]):
    """(closes2, info): no jump -> (closes, None); one jump -> earlier segment
    rescaled to the later unit + info{date,ratio,factor}; >1 jump ->
    (closes unchanged, {"multi": n}) so the caller can skip returns."""
    jumps = []  # (index i where closes[i]/closes[i-1] leaves the band, ratio)
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if not a or not b:  # skip None / 0
            continue
        r = b / a
        if r >= UNIT_JUMP_HI or r <= UNIT_JUMP_LO:
            jumps.append((i, r))
    if not jumps:
        return closes, None
    if len(jumps) > 1:
        return closes, {"multi": len(jumps)}
    i, r = jumps[0]
    factor = 100.0 if r >= UNIT_JUMP_HI else 0.01  # up-jump: earlier*100; down-jump: earlier/100
    fixed = [(c * factor if (idx < i and c is not None) else c) for idx, c in enumerate(closes)]
    return fixed, {"date": dates[i], "ratio": r, "factor": factor}


# Sub-unit currencies Yahoo quotes in 1/100 of the major unit: agorot (ILA) on
# TASE, pence (GBp/GBX) on the LSE. This is the ONE place the ÷100 happens — a
# native sub-unit price is divided by 100 and stored in the major currency, so
# stored quotes are always in the major unit (never agorot/pence).
SUBUNIT_CURRENCY = {
    "ILA": ("ILS", 0.01),  # TASE agorot
    "GBp": ("GBP", 0.01),  # LSE pence (Yahoo lower-case p)
    "GBX": ("GBP", 0.01),  # LSE pence (alt code)
}


def normalize_currency(native: str | None) -> tuple[str, float]:
    """(display_currency, price_scale) for a Yahoo-reported currency. Sub-unit
    currencies (agorot, pence) map to their major unit at 0.01; everything else
    passes through unscaled. None -> ('USD', 1.0)."""
    if not native:
        return ("USD", 1.0)
    return SUBUNIT_CURRENCY.get(native, (native, 1.0))


def currency_for(symbol: str, cached: str | None) -> str:
    """Native quote currency from Yahoo, cached via quotes.currency.

    The stored value is the major (post-conversion) currency, so this maps it
    back to the native sub-unit by suffix — 'ILS'->'ILA' for .TA (agorot),
    'GBP'->'GBp' for .L (pence) — so the ÷100 rule keeps applying on re-runs.
    """
    if cached:
        if cached == "ILS" and symbol.endswith(".TA"):
            return "ILA"
        if cached == "GBP" and symbol.endswith(".L"):
            return "GBp"
        return cached
    try:
        cur = yf.Ticker(symbol).fast_info["currency"]
        if cur:
            return cur
    except Exception as e:
        log.warning("currency lookup failed for %s (%s); falling back on suffix", symbol, e)
    if symbol.endswith(".TA"):
        return "ILA"
    if symbol.endswith(".L"):
        return "GBp"
    return "USD"


def persist_history(engine, sec_id: str, dates: list[date], closes: list[float], scale: float, today: date) -> int:
    """Upsert the last ~CHART_DAYS of NORMALIZED daily closes. Returns rows written.

    `scale` is the SAME factor already applied to quotes.last_price for this
    security (1.0, or 0.01 for agorot/pence), so the stored close always matches
    the watchlist number — the ÷100 is never repeated or skipped here.

    ON CONFLICT DO UPDATE, so re-runs are idempotent and a later Yahoo
    correction/adjustment to a past close overwrites the old value.
    """
    cutoff = today - timedelta(days=CHART_DAYS)
    rows = [
        {"sec_id": sec_id, "price_date": d, "close": c * scale}
        for d, c in zip(dates, closes)
        # closes_series already dropped NaNs; belt-and-braces, since a NaN here
        # would poison the chart with a non-price.
        if d >= cutoff and c is not None and not math.isnan(c)
    ]
    if not rows:
        return 0
    with engine.begin() as conn:
        conn.execute(upsert_many(engine, price_history, ["sec_id", "price_date"], rows), rows)
    return len(rows)


def prune_history(engine, today: date) -> int:
    """Drop closes older than the retention window. Keeps the table bounded —
    it grows by ~250 rows per security per year otherwise."""
    cutoff = today - timedelta(days=RETENTION_DAYS)
    with engine.begin() as conn:
        result = conn.execute(price_history.delete().where(price_history.c.price_date < cutoff))
    return result.rowcount or 0


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

    history_rows = 0
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
            # Fix a single agorot<->shekel unit discontinuity so returns AND
            # persisted history consume the same single-scale array (one truth).
            closes, jump_info = normalize_unit_jumps(dates, closes)
            if jump_info and "multi" not in jump_info:
                log.info(
                    "UNIT_JUMP sec_id=%s date=%s ratio=%.3f action=pre_x%s",
                    sec_id, jump_info["date"], jump_info["ratio"],
                    "100" if jump_info["factor"] == 100.0 else "0.01",
                )
            native_ccy = currency_for(symbol, prior["currency"] if prior else None)
            display_ccy, scale = normalize_currency(native_ccy)  # agorot/pence -> major unit ÷100
            last, last_date = closes[-1], dates[-1]
            prev = closes[-2] if len(closes) > 1 else None
            values = {
                "sec_id": sec_id,
                "last_price": last * scale,
                "prev_close": prev * scale if prev is not None else None,
                "day_change_pct": pct_change(last, prev),
                "currency": display_ccy,
                "as_of": datetime.combine(last_date, time.min, tzinfo=timezone.utc),
                "source": "yfinance",
                "status": "ok",
            }
            if need_anchors and (prior is None or prior["anchors_date"] != today):
                if jump_info and jump_info.get("multi"):
                    # Ambiguous unit history — don't guess a scale; NULL the
                    # period returns this run (current quote fields still write).
                    returns = {c: None for c in PERIOD_COLS}
                    log.info("UNIT_JUMP sec_id=%s jumps=%s action=returns_skipped", sec_id, jump_info["multi"])
                else:
                    returns = period_returns(dates, closes, last, today)
                    missing = [c for c, v in returns.items() if v is None]
                    if missing:
                        log.info("%s (%s): history since %s -> %s = NULL", sec_id, symbol, dates[0], ",".join(missing))
                values.update(returns)
                values["anchors_date"] = today
            # Only on the daily anchor refresh: that's the run holding the full
            # ~400d frame. The short (12d) intra-day runs would just rewrite the
            # same recent closes for no gain.
            if need_anchors:
                n = persist_history(engine, sec_id, dates, closes, scale, today)
                history_rows += n
                log.info("%s (%s): %d history closes persisted", sec_id, symbol, n)
            log.info("%s (%s): last=%.2f %s as_of=%s", sec_id, symbol, values["last_price"], values["currency"], last_date)

        with engine.begin() as conn:
            conn.execute(upsert(engine, quotes, ["sec_id"], values))

    if history_rows:
        log.info("auto tier: %d history rows written", history_rows)


def collect_manual(engine, secs: list[dict], today: date) -> None:
    history_rows = 0
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
            # Mirror the entered points as-is. manual_price CLI takes ILS (not
            # agorot), so scale is 1.0 — no conversion, and nothing is invented
            # or interpolated between points. The series is sparse by nature;
            # the chart shows exactly the points that exist.
            n = persist_history(engine, sec_id, dates, closes, 1.0, today)
            history_rows += n
            log.info(
                "%s (%s): last=%.2f %s as_of=%s (manual, %d history points)",
                sec_id, sec["symbol"], last, values["currency"], last_date, n,
            )
        with engine.begin() as conn:
            conn.execute(upsert(engine, quotes, ["sec_id"], values))

    if history_rows:
        log.info("manual tier: %d history rows written", history_rows)


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
    pruned = prune_history(engine, today)
    log.info(
        "done: auto=%d manual=%d skipped=%d history_pruned=%d",
        len(auto), len(manual), len(other), pruned,
    )


if __name__ == "__main__":
    collect()
