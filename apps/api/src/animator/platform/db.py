"""The platform database: SQLAlchemy async engine and sessions, migrated by Alembic at startup.

SQLite (default) runs in WAL mode with foreign keys on and a busy timeout, so readers never wait for
the writer and short write bursts queue instead of failing. Any other async URL (PostgreSQL) works
unchanged: the queries are plain SQLAlchemy.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(connection: Connection | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=15000")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


class Database:
    def __init__(self, url: str, echo: bool = False) -> None:
        self.url = url
        self.sqlite = url.startswith("sqlite")
        kwargs: dict[str, Any] = {"echo": echo}
        if self.sqlite:
            kwargs["connect_args"] = {"timeout": 15}
            if url.rstrip("/").endswith(":memory:") or url in ("sqlite+aiosqlite://", "sqlite+aiosqlite:///"):
                kwargs["poolclass"] = StaticPool
        else:
            kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=20)
        self.engine = create_async_engine(url, **kwargs)
        if self.sqlite:
            event.listen(self.engine.sync_engine, "connect", _sqlite_pragmas)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def migrate(self) -> None:
        """Bring the schema to the latest version (idempotent)."""

        def upgrade(connection: Connection) -> None:
            command.upgrade(alembic_config(connection), "head")

        async with self.engine.begin() as conn:
            await conn.run_sync(upgrade)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as s:
            yield s

    async def close(self) -> None:
        await self.engine.dispose()
