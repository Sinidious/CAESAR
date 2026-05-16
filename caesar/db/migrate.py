"""Programmatic Alembic helpers.

Alembic itself runs synchronously; we convert the async SQLite URL
(``sqlite+aiosqlite://...``) into its sync sibling (``sqlite://...``)
before handing it over. The Alembic script directory lives inside the
package at ``caesar/db/migrations/``.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def _sync_url(async_url: str) -> str:
    """Strip async drivers so Alembic can use the sync DBAPI."""

    return async_url.replace("+aiosqlite", "")


def alembic_config(async_url: str) -> Config:
    """Build an Alembic Config pointing at the in-package migrations."""

    here = Path(__file__).resolve().parent
    cfg = Config()
    cfg.set_main_option("script_location", str(here / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _sync_url(async_url))
    return cfg


def upgrade_to_head(async_url: str) -> None:
    """Apply every outstanding migration."""

    command.upgrade(alembic_config(async_url), "head")
