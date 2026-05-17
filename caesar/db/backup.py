"""Hot-safe backup + verified restore for the SQLite database (ADR-0022).

The Online Backup API (``sqlite3.Connection.backup``) serialises
against any concurrent writes, so :func:`backup_to` is safe to run
while Praetor is up. :func:`restore_from` is NOT — the caller must
stop the service first; the CLI wrapper enforces a confirmation flag.

Both functions are sync. They're called from the Typer CLI in a
fresh process, not from the async request path.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url


class BackupError(RuntimeError):
    """A backup or restore precondition failed."""


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of inspecting a candidate source backup."""

    integrity: str
    has_audit_log: bool


def sqlite_path_from_url(url: str) -> Path:
    """Extract the on-disk path from a SQLite URL.

    Raises ``BackupError`` for URLs that don't refer to a real file
    (``:memory:``, non-SQLite drivers, etc.).
    """

    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):
        raise BackupError(f"backup/restore is SQLite-only; got driver {parsed.drivername!r}")
    database = parsed.database
    if database is None or database == ":memory:":
        raise BackupError("backup/restore requires a file-backed database")
    return Path(database)


def verify_backup(source: Path) -> VerifyResult:
    """Open ``source`` and confirm it looks like a CAESAR backup."""

    if not source.is_file():
        raise BackupError(f"source backup {source} does not exist")
    conn = sqlite3.connect(str(source))
    try:
        try:
            integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
            integrity = "" if integrity_row is None else str(integrity_row[0])
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        except sqlite3.DatabaseError as exc:
            raise BackupError(
                f"source backup {source} is not a valid SQLite database: {exc}"
            ) from exc
    finally:
        conn.close()
    if integrity != "ok":
        raise BackupError(f"source backup {source} failed integrity check: {integrity}")
    has_audit = "audit_log" in tables
    if not has_audit:
        raise BackupError(
            f"source backup {source} does not look like a CAESAR snapshot (no audit_log table)"
        )
    return VerifyResult(integrity=integrity, has_audit_log=has_audit)


def backup_to(db_url: str, destination: Path, *, overwrite: bool = False) -> Path:
    """Snapshot the live DB to ``destination``. Hot-safe."""

    source = sqlite_path_from_url(db_url)
    if not source.is_file():
        raise BackupError(f"source DB {source} does not exist; run `caesar praetor migrate` first")
    if destination.exists() and not overwrite:
        raise BackupError(f"destination {destination} exists; pass --overwrite to replace it")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    src_conn = sqlite3.connect(str(source))
    dst_conn = sqlite3.connect(str(destination))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()
    return destination


def restore_from(db_url: str, source: Path, *, force: bool = False) -> Path:
    """Replace the live DB with ``source``. Praetor must be stopped."""

    verify_backup(source)
    destination = sqlite_path_from_url(db_url)
    if destination.exists() and not force:
        raise BackupError(
            f"destination {destination} exists; pass --force to overwrite (stop Praetor first)"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    src_conn = sqlite3.connect(str(source))
    dst_conn = sqlite3.connect(str(destination))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()
    return destination
