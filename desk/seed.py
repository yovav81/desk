"""Idempotent seed loader: data/securities.csv + data/watchlist_seed.csv -> DESK_DB_URL.

Safe to re-run: uses insert_ignore() (INSERT ... ON CONFLICT DO NOTHING) for
every insert, so a second run always inserts 0 new rows.
"""
import csv
from pathlib import Path

from sqlalchemy import func, select

from desk.db import get_engine, init_db, insert_ignore, securities, users, watchlist

ROOT = Path(__file__).resolve().parent.parent
SECURITIES_CSV = ROOT / "data" / "securities.csv"
WATCHLIST_CSV = ROOT / "data" / "watchlist_seed.csv"


def seed(engine=None) -> None:
    engine = engine or get_engine()
    init_db(engine)

    with open(SECURITIES_CSV, newline="", encoding="utf-8") as f:
        sec_rows = list(csv.DictReader(f))
    with engine.begin() as conn:
        for row in sec_rows:
            conn.execute(insert_ignore(engine, securities, ["sec_id"]).values(**row))

    with open(WATCHLIST_CSV, newline="", encoding="utf-8") as f:
        wl_rows = list(csv.DictReader(f))
    with engine.begin() as conn:
        for row in wl_rows:
            conn.execute(insert_ignore(engine, users, ["username"]).values(username=row["username"]))
        user_map = {r.username: r.id for r in conn.execute(select(users))}
        for row in wl_rows:
            conn.execute(
                insert_ignore(engine, watchlist, ["user_id", "sec_id"]).values(
                    user_id=user_map[row["username"]], sec_id=row["sec_id"]
                )
            )

    with engine.connect() as conn:
        sec_count = conn.execute(select(func.count()).select_from(securities)).scalar()
        wl_count = conn.execute(select(func.count()).select_from(watchlist)).scalar()
    print(f"securities: {sec_count} row(s)")
    print(f"watchlist: {wl_count} row(s)")


if __name__ == "__main__":
    seed()
