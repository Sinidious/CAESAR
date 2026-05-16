"""Audit-log writer (ADR-0012).

Every brain decision lands here in the same transaction as the work
that produced it. The row is the API — keep the columns stable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.schema import audit_log


class AuditLogger:
    """Synchronous (within the request) audit-log writer."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record(self, event_type: str, payload: dict[str, Any]) -> int:
        """Persist one event. Returns the new row id."""

        async with self._engine.begin() as conn:
            result = await conn.execute(
                insert(audit_log).values(
                    ts=datetime.now(UTC),
                    event_type=event_type,
                    payload=payload,
                )
            )
            row_id = result.inserted_primary_key
            if row_id is None:  # pragma: no cover - defensive
                raise RuntimeError("audit_log insert returned no id")
            return int(row_id[0])
