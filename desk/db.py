"""SQLAlchemy Core schema for DESK. Runs on SQLite locally and Postgres when hosted.

DESK_DB_URL selects the backend, e.g.:
  sqlite:///desk.db                              (local dev default)
  postgresql+psycopg://user:pass@host/dbname     (hosted)
"""
import os

from sqlalchemy import (
    Column,
    DateTime,
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
)

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
    Column("price_source", String(16), nullable=False, server_default="yfinance"),
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


def get_engine(db_url: str | None = None):
    url = db_url or os.environ.get("DESK_DB_URL", "sqlite:///desk.db")
    return create_engine(url, future=True)


def insert_ignore(engine, table: Table, index_elements: list[str]):
    """Return an INSERT ... ON CONFLICT DO NOTHING construct for the engine's dialect."""
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(table).on_conflict_do_nothing(index_elements=index_elements)
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return sqlite_insert(table).on_conflict_do_nothing(index_elements=index_elements)


def init_db(engine=None) -> None:
    """Create all tables if they don't exist, and seed the default user."""
    engine = engine or get_engine()
    metadata.create_all(engine, checkfirst=True)
    default_user = os.environ.get("DESK_DEFAULT_USER", "owner")
    with engine.begin() as conn:
        stmt = insert_ignore(engine, users, ["username"]).values(username=default_user)
        conn.execute(stmt)


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {get_engine().url}")
