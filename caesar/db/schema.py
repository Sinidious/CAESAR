"""Schema declarations (SQLAlchemy Core).

The audit log table is intentionally documented as the public shape —
the dashboard reads from it, replay reads from it, and operators read
from it. Per ADR-0012 the row is the API.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
)
from sqlalchemy import Column as C

metadata = MetaData()

audit_log = Table(
    "audit_log",
    metadata,
    C("id", Integer, primary_key=True, autoincrement=True),
    C("ts", DateTime(timezone=True), nullable=False, index=True),
    C("event_type", String(64), nullable=False, index=True),
    C("payload", JSON, nullable=False),
)
"""Append-only record of every brain decision (ADR-0012)."""
