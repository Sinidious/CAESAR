"""Async SQLAlchemy engine + connection-init hardening (ADR-0019).

Every new connection gets ``journal_mode=WAL``,
``synchronous=NORMAL``, and ``foreign_keys=ON`` so callers can't forget
them. For SQLite URLs the parent directory is created lazily on first
engine construction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def _ensure_sqlite_parent_dir(url: str) -> None:
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):  # pragma: no cover
        return
    database = parsed.database
    if database is None or database == ":memory:":
        return
    Path(database).parent.mkdir(parents=True, exist_ok=True)


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Register a connect listener that hardens every new connection."""

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Build an async engine with our standard hardening."""

    _ensure_sqlite_parent_dir(url)
    engine = create_async_engine(url, echo=echo, future=True)
    if make_url(url).drivername.startswith("sqlite"):  # pragma: no branch
        _install_sqlite_pragmas(engine)
    return engine
