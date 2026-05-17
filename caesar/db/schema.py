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


app_settings = Table(
    "app_settings",
    metadata,
    C("key", String(64), primary_key=True),
    C("value", String, nullable=False),
    C("updated_at", DateTime(timezone=True), nullable=False),
)
"""Operator-tunable runtime settings.

A flat key/value store. v0.5 writes ``llm.system_prompt`` here from
the dashboard so operators can adjust CAESAR's voice without an env
var + restart. The env-loaded ``CaesarSettings`` is still the default
when no row is present.
"""


semantic_chunks = Table(
    "semantic_chunks",
    metadata,
    C("id", Integer, primary_key=True, autoincrement=True),
    C("audit_log_id", Integer, nullable=False, unique=True, index=True),
    C("text", String, nullable=False),
    C("embedding", JSON, nullable=False),
    C("model", String(64), nullable=False),
    C("created_at", DateTime(timezone=True), nullable=False),
)
"""Embedded slices of episodic memory (ADR-0010 amendment).

One row per indexed ``audit_log`` row; ``audit_log_id`` is unique so
the indexer can re-run idempotently. Embedding is stored as a JSON
array of floats; v0.4 ranks candidates with Python-side cosine. A
later milestone will swap the search path to a vector-index extension
without changing this schema.
"""
