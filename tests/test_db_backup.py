"""Tests for the hot-safe backup/restore helpers (ADR-0022)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from caesar.db.backup import (
    BackupError,
    backup_to,
    restore_from,
    sqlite_path_from_url,
    verify_backup,
)
from caesar.db.migrate import upgrade_to_head


def _make_caesar_db(path: Path) -> None:
    """Create a real CAESAR DB at ``path`` with one audit row."""

    url = f"sqlite+aiosqlite:///{path}"
    upgrade_to_head(url)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO audit_log (ts, event_type, payload) VALUES (?, ?, ?)",
            (datetime.now(UTC).isoformat(), "chat.completed", '{"reply":"hi"}'),
        )
        conn.commit()
    finally:
        conn.close()


# --- sqlite_path_from_url ----------------------------------------------------


def test_sqlite_path_from_url_extracts_filename(tmp_path: Path) -> None:
    p = sqlite_path_from_url(f"sqlite+aiosqlite:///{tmp_path / 'x.sqlite3'}")
    assert p == tmp_path / "x.sqlite3"


def test_sqlite_path_from_url_rejects_non_sqlite() -> None:
    with pytest.raises(BackupError, match="SQLite-only"):
        sqlite_path_from_url("postgresql://localhost/x")


def test_sqlite_path_from_url_rejects_memory() -> None:
    with pytest.raises(BackupError, match="file-backed"):
        sqlite_path_from_url("sqlite+aiosqlite:///:memory:")


# --- verify_backup -----------------------------------------------------------


def test_verify_backup_accepts_real_caesar_db(tmp_path: Path) -> None:
    src = tmp_path / "src.sqlite3"
    _make_caesar_db(src)
    result = verify_backup(src)
    assert result.integrity == "ok"
    assert result.has_audit_log is True


def test_verify_backup_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="does not exist"):
        verify_backup(tmp_path / "nope.sqlite3")


def test_verify_backup_rejects_non_caesar_db(tmp_path: Path) -> None:
    """A valid SQLite file without our audit_log table is rejected."""

    bogus = tmp_path / "bogus.sqlite3"
    conn = sqlite3.connect(str(bogus))
    conn.execute("CREATE TABLE wrong_thing (id INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(BackupError, match="no audit_log table"):
        verify_backup(bogus)


# --- backup_to ---------------------------------------------------------------


def test_backup_round_trip_preserves_rows(tmp_path: Path) -> None:
    src = tmp_path / "src.sqlite3"
    _make_caesar_db(src)
    dest = tmp_path / "snap.sqlite3"

    result = backup_to(f"sqlite+aiosqlite:///{src}", dest)
    assert result == dest
    assert dest.is_file()

    conn = sqlite3.connect(str(dest))
    try:
        rows = conn.execute("SELECT event_type FROM audit_log").fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == ["chat.completed"]


def test_backup_refuses_to_overwrite_without_flag(tmp_path: Path) -> None:
    src = tmp_path / "src.sqlite3"
    _make_caesar_db(src)
    dest = tmp_path / "snap.sqlite3"
    dest.write_bytes(b"existing")

    with pytest.raises(BackupError, match="exists"):
        backup_to(f"sqlite+aiosqlite:///{src}", dest)


def test_backup_overwrites_with_flag(tmp_path: Path) -> None:
    src = tmp_path / "src.sqlite3"
    _make_caesar_db(src)
    dest = tmp_path / "snap.sqlite3"
    dest.write_bytes(b"existing")

    backup_to(f"sqlite+aiosqlite:///{src}", dest, overwrite=True)
    # Verify the new file is a valid CAESAR snapshot.
    verify_backup(dest)


def test_backup_rejects_missing_source(tmp_path: Path) -> None:
    dest = tmp_path / "snap.sqlite3"
    with pytest.raises(BackupError, match="does not exist"):
        backup_to(f"sqlite+aiosqlite:///{tmp_path / 'missing.sqlite3'}", dest)


def test_backup_creates_destination_parent(tmp_path: Path) -> None:
    src = tmp_path / "src.sqlite3"
    _make_caesar_db(src)
    dest = tmp_path / "nested" / "dirs" / "snap.sqlite3"

    backup_to(f"sqlite+aiosqlite:///{src}", dest)
    assert dest.is_file()


# --- restore_from ------------------------------------------------------------


def test_restore_replaces_destination(tmp_path: Path) -> None:
    snap = tmp_path / "snap.sqlite3"
    _make_caesar_db(snap)

    dest = tmp_path / "live.sqlite3"
    url = f"sqlite+aiosqlite:///{dest}"

    restore_from(url, snap)
    verify_backup(dest)


def test_restore_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    snap = tmp_path / "snap.sqlite3"
    _make_caesar_db(snap)
    dest = tmp_path / "live.sqlite3"
    dest.write_bytes(b"existing")

    with pytest.raises(BackupError, match="exists"):
        restore_from(f"sqlite+aiosqlite:///{dest}", snap)


def test_restore_with_force_overwrites(tmp_path: Path) -> None:
    snap = tmp_path / "snap.sqlite3"
    _make_caesar_db(snap)
    dest = tmp_path / "live.sqlite3"
    dest.write_bytes(b"existing")

    restore_from(f"sqlite+aiosqlite:///{dest}", snap, force=True)
    verify_backup(dest)


def test_restore_rejects_invalid_source(tmp_path: Path) -> None:
    bad = tmp_path / "not-a-db.sqlite3"
    bad.write_bytes(b"hello")
    dest = tmp_path / "live.sqlite3"
    with pytest.raises(BackupError):
        restore_from(f"sqlite+aiosqlite:///{dest}", bad)
