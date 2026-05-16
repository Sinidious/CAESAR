from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from caesar.db.migrate import _sync_url, alembic_config, upgrade_to_head


def test_sync_url_strips_aiosqlite():
    assert _sync_url("sqlite+aiosqlite:///x.db") == "sqlite:///x.db"
    # No-op for non-aiosqlite URLs.
    assert _sync_url("sqlite:///x.db") == "sqlite:///x.db"


def test_alembic_config_points_at_package_migrations(tmp_path: Path):
    cfg = alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'c.sqlite3'}")
    script_location = cfg.get_main_option("script_location")
    assert script_location is not None
    assert script_location.endswith("migrations")
    assert Path(script_location).is_dir()


async def test_upgrade_creates_audit_log(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'migrate.sqlite3'}"
    upgrade_to_head(url)

    eng = create_async_engine(url)
    try:
        async with eng.connect() as conn:

            def _tables(sync_conn):
                return inspect(sync_conn).get_table_names()

            tables = await conn.run_sync(_tables)
        assert "audit_log" in tables
    finally:
        await eng.dispose()
