"""Audit-log writer (ADR-0012).

Every brain decision lands here in the same transaction as the work
that produced it. The row is the API — keep the columns stable.

When constructed with an :class:`AuditEventBus`, every successful
write also fans out to the dashboard's live SSE subscribers
(ADR-0021). The bus is best-effort; the DB row is the source of truth.

Payload strings longer than ``max_string_chars`` are clamped at write
time (SR-008) so a pathological LLM output can't bloat the table.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit_clamp import clamp_payload
from caesar.db.schema import audit_log
from caesar.log import get_logger
from caesar.metrics import AUDIT_EVENTS

if TYPE_CHECKING:
    from caesar.praetor.audit_bus import AuditEventBus

logger = get_logger("caesar.db.audit")

DEFAULT_MAX_STRING_CHARS = 16384


class AuditLogger:
    """Synchronous (within the request) audit-log writer."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        bus: AuditEventBus | None = None,
        max_string_chars: int = DEFAULT_MAX_STRING_CHARS,
    ) -> None:
        self._engine = engine
        self._bus = bus
        self._max_string_chars = max_string_chars

    async def record(self, event_type: str, payload: dict[str, Any]) -> int:
        """Persist one event. Returns the new row id."""

        clamped, truncated = clamp_payload(payload, max_chars=self._max_string_chars)
        if truncated:
            logger.warning(
                "audit.payload_truncated",
                event_type=event_type,
                max_string_chars=self._max_string_chars,
            )
        ts = datetime.now(UTC)
        async with self._engine.begin() as conn:
            result = await conn.execute(
                insert(audit_log).values(
                    ts=ts,
                    event_type=event_type,
                    payload=clamped,
                )
            )
            row_id = result.inserted_primary_key
            if row_id is None:  # pragma: no cover - defensive
                raise RuntimeError("audit_log insert returned no id")
            new_id = int(row_id[0])
        AUDIT_EVENTS.labels(event_type=event_type).inc()
        if self._bus is not None:
            self._bus.publish(
                {
                    "id": new_id,
                    "ts": ts.isoformat(),
                    "event_type": event_type,
                    "payload": clamped,
                }
            )
        return new_id
