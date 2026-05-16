from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from caesar.db.engine import create_engine


async def test_create_engine_creates_parent_dir(tmp_path: Path):
    nested = tmp_path / "a" / "b" / "c" / "caesar.sqlite3"
    url = f"sqlite+aiosqlite:///{nested}"
    eng = create_engine(url)
    try:
        assert nested.parent.exists()
        async with eng.connect() as conn:
            row = (await conn.execute(text("SELECT 1"))).scalar_one()
            assert row == 1
    finally:
        await eng.dispose()


async def test_pragmas_are_applied(tmp_path: Path):
    eng = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'p.sqlite3'}")
    try:
        async with eng.connect() as conn:
            journal = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
            assert journal == "wal"
            sync = (await conn.execute(text("PRAGMA synchronous"))).scalar_one()
            # NORMAL == 1
            assert int(sync) == 1
            fk = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
            assert int(fk) == 1
    finally:
        await eng.dispose()


async def test_memory_url_skips_dir_creation():
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with eng.connect() as conn:
            row = (await conn.execute(text("SELECT 2"))).scalar_one()
            assert row == 2
    finally:
        await eng.dispose()
