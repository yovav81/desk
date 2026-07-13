"""SQLAlchemy Core schema for DESK. Runs on SQLite locally and Postgres when hosted.

DESK_DB_URL selects the backend, e.g.:
  sqlite:///desk.db                              (local dev default)
  postgresql+psycopg://user:pass@host/dbname     (hosted)
"""
import os

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    text,
)
from sqlalchemy.engine import make_url

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(255), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

securities = Table(
    "securities",
    metadata,
    Column("sec_id", String(32), primary_key=True),
    Column("symbol", String(32), nullable=False),
    Column("name", String(255), nullable=False),
    Column("asset_type", String(16), nullable=False),  # stock | bond
    Column("market", String(16), nullable=False),  # US | TASE
    Column("price_source", String(16), nullable=False, server_default="yfinance"),  # yfinance | manual
    Column("yahoo_symbol", String(32), nullable=True),  # override; default resolution in securities.py
)

watchlist = Table(
    "watchlist",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("sec_id", String(32), ForeignKey("securities.sec_id"), nullable=False),
    Column("added_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    UniqueConstraint("user_id", "sec_id", name="uq_watchlist_user_sec"),
)
Index("ix_watchlist_user_id", watchlist.c.user_id)

news = Table(
    "news",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("sec_id", String(32), ForeignKey("securities.sec_id"), nullable=True),
    Column("source", String(64), nullable=False),
    Column("title", Text, nullable=False),
    Column("url", Text, nullable=False, unique=True),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("fetched_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("summary", Text, nullable=True),
)
Index("ix_news_published_at", news.c.published_at)

emails = Table(
    "emails",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("sec_id", String(32), ForeignKey("securities.sec_id"), nullable=True),
    Column("sender", String(255), nullable=False),
    Column("subject", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=True),
    Column("body_text", Text, nullable=True),
    Column("matched_by", String(16), nullable=True),  # sender | subject | body
    Column("message_id", String(998), nullable=False, unique=True),
)
Index("ix_emails_received_at", emails.c.received_at)

quotes = Table(
    "quotes",
    metadata,
    Column("sec_id", String(32), ForeignKey("securities.sec_id"), primary_key=True),
    Column("last_price", Float, nullable=True),
    Column("prev_close", Float, nullable=True),
    Column("day_change_pct", Float, nullable=True),
    Column("mtd_pct", Float, nullable=True),
    Column("qtd_pct", Float, nullable=True),
    Column("ytd_pct", Float, nullable=True),
    Column("y12_pct", Float, nullable=True),
    Column("currency", String(8), nullable=True),  # always post-conversion (ILS, never ILA)
    Column("as_of", DateTime(timezone=True), nullable=True),  # date of last_price
    Column("anchors_date", Date, nullable=True),  # calendar day the period anchors were last computed
    Column("source", String(16), nullable=False),  # yfinance | manual
    Column("status", String(16), nullable=False),  # ok | no_data | stale
)

manual_prices = Table(
    "manual_prices",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("sec_id", String(32), ForeignKey("securities.sec_id"), nullable=False),
    Column("price_date", Date, nullable=False),
    Column("close", Float, nullable=False),
    UniqueConstraint("sec_id", "price_date", name="uq_manual_prices_sec_date"),
)


def _needs_prepared_statements_disabled(url) -> bool:
    """True for Postgres behind a transaction pooler (pgbouncer), where
    psycopg3 server-side prepared statements collide ("prepared statement
    '_pg3_0' already exists"). Detects Supabase's pooler host and the
    conventional transaction-pooler port 6543."""
    if not url.drivername.startswith("postgresql"):
        return False
    host = (url.host or "").lower()
    return "pooler.supabase.com" in host or url.port == 6543


def get_engine(db_url: str | None = None):
    raw = db_url or os.environ.get("DESK_DB_URL", "sqlite:///desk.db")
    url = make_url(raw)
    connect_args: dict = {}
    if _needs_prepared_statements_disabled(url):
        # psycopg3: prepare_threshold=None never issues a server-side PREPARE,
        # which is what the transaction pooler cannot share across connections.
        connect_args["prepare_threshold"] = None
    return create_engine(url, future=True, connect_args=connect_args)


def insert_ignore(engine, table: Table, index_elements: list[str]):
    """Return an INSERT ... ON CONFLICT DO NOTHING construct for the engine's dialect."""
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(table).on_conflict_do_nothing(index_elements=index_elements)
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return sqlite_insert(table).on_conflict_do_nothing(index_elements=index_elements)


def upsert(engine, table: Table, index_elements: list[str], values: dict):
    """Return an INSERT ... ON CONFLICT DO UPDATE construct for the engine's dialect.

    Updates every column present in `values` except the conflict keys.
    """
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    stmt = dialect_insert(table).values(**values)
    set_ = {k: stmt.excluded[k] for k in values if k not in index_elements}
    return stmt.on_conflict_do_update(index_elements=index_elements, set_=set_)


def _migrate(engine) -> None:
    """Idempotent additive migrations for DBs created before newer columns existed."""
    insp = inspect(engine)
    if "securities" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("securities")}
        if "yahoo_symbol" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE securities ADD COLUMN yahoo_symbol VARCHAR(32)"))


def init_db(engine=None) -> None:
    """Create all tables if they don't exist, and seed the default user."""
    engine = engine or get_engine()
    _migrate(engine)
    metadata.create_all(engine, checkfirst=True)
    default_user = os.environ.get("DESK_DEFAULT_USER", "owner")
    with engine.begin() as conn:
        stmt = insert_ignore(engine, users, ["username"]).values(username=default_user)
        conn.execute(stmt)


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {get_engine().url}")
