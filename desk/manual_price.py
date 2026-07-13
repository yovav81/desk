"""Upsert a manually-entered price point for a manual-tier security.

Usage: python -m desk.manual_price <sec_id> <YYYY-MM-DD> <close>

Re-entering the same (sec_id, date) updates the close (ON CONFLICT DO UPDATE).
Prices for TASE securities are entered in ILS (shekels), not agorot.
"""
import sys
from datetime import date

from sqlalchemy import select

from desk.db import get_engine, init_db, manual_prices, securities, upsert


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    sec_id, date_str, close_str = argv
    try:
        price_date = date.fromisoformat(date_str)
        close = float(close_str)
    except ValueError as e:
        print(f"invalid argument: {e}", file=sys.stderr)
        return 2

    engine = get_engine()
    init_db(engine)
    with engine.begin() as conn:
        sec = conn.execute(select(securities).where(securities.c.sec_id == sec_id)).first()
        if sec is None:
            print(f"unknown sec_id {sec_id!r} — seed it into securities first", file=sys.stderr)
            return 1
        conn.execute(
            upsert(
                engine,
                manual_prices,
                ["sec_id", "price_date"],
                {"sec_id": sec_id, "price_date": price_date, "close": close},
            )
        )
    print(f"{sec_id} ({sec.name}): {price_date} close={close}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
