"""SQLAlchemy models + engine setup.

SQLite (aiosqlite) by default so the demo needs zero infra; DATABASE_URL
swaps in Postgres without touching any other file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DeckRow(Base):
    __tablename__ = "decks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    source_filename: Mapped[str] = mapped_column(String(300), default="")
    source_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pdf_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="uploaded", index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    slide_count: Mapped[int] = mapped_column(Integer, default=0)
    intro: Mapped[str] = mapped_column(Text, default="")
    outro: Mapped[str] = mapped_column(Text, default="")
    persona: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SlideRow(Base):
    __tablename__ = "slides"
    __table_args__ = (UniqueConstraint("deck_id", "number", name="uq_slides_deck_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deck_id: Mapped[str] = mapped_column(String(32), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), default="")
    bullets: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    notes: Mapped[str] = mapped_column(Text, default="")
    transition: Mapped[str] = mapped_column(Text, default="")
    image_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    deck_id: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    transcript: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    events: Mapped[str] = mapped_column(Text, default="[]")  # JSON


# Columns added after the first release. create_all() only creates missing
# tables, so pre-existing SQLite files need these bolted on at startup.
_MIGRATIONS = [
    ("decks", "intro", "TEXT NOT NULL DEFAULT ''"),
    ("decks", "outro", "TEXT NOT NULL DEFAULT ''"),
    ("decks", "persona", "TEXT NOT NULL DEFAULT ''"),
    ("slides", "transition", "TEXT NOT NULL DEFAULT ''"),
]


def _migrate_sqlite(conn) -> None:
    for table, column, ddl in _MIGRATIONS:
        existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


async def make_engine(database_url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if database_url.startswith("sqlite"):
            await conn.run_sync(_migrate_sqlite)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
