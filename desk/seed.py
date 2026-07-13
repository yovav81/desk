"""Idempotent seed loader: data/securities.csv + data/watchlist_seed.csv -> DESK_DB_URL.

Safe to re-run. `securities` rows are upserted so CSV edits (new tickers,
corrected symbols, yahoo_symbol overrides) propagate to an already-seeded DB;
`users`/`watchlist` use insert_ignore so re-runs add no duplicate rows.
"""
import csv
from pathlib import Path

from sqlalchemy import func, select

from desk.db import get_engine, init_db, insert_ignore, securities, upsert, users, watchlist

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
            values = {k: (v or None) for k, v in row.items()}  # empty CSV cells (yahoo_symbol) -> NULL
            conn.execute(upsert(engine, securities, ["sec_id"], values))

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
