"""Memory-recall worker (ADR-0010 + ADR-0012).

First real Legion worker. Reads the most recent rows from the
``audit_log`` table and returns them as structured events the brain
can fold back into its context.

Input payload:

.. code-block:: json

    {
        "limit": 10,                       // optional, default 10
        "event_type": "chat.completed"     // optional filter
    }

Output:

.. code-block:: json

    {
        "events": [
            {
                "id": 17,
                "ts": "2026-05-17T02:00:00+00:00",
                "event_type": "chat.completed",
                "payload": {...}
            },
            ...
        ]
    }

Events are returned newest first.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.bus.client import Bus
from caesar.db.schema import audit_log
from caesar.legion.protocol import TaskDispatch
from caesar.legion.worker import Worker

CAPABILITY = "memory.recall"
WORKER_ID = "memory_recall"


class MemoryRecallWorker(Worker):
    """Recall recent audit rows for the brain."""

    worker_id: ClassVar[str] = WORKER_ID
    capabilities: ClassVar[list[str]] = [CAPABILITY]
    version: ClassVar[str] = "0.1.3"

    def __init__(
        self,
        bus: Bus,
        engine: AsyncEngine,
        *,
        default_limit: int = 10,
        max_limit: int = 100,
    ) -> None:
        super().__init__(bus)
        self._engine = engine
        self._default_limit = default_limit
        self._max_limit = max_limit

    async def handle(self, task: TaskDispatch) -> dict[str, object]:
        raw_limit = task.payload.get("limit", self._default_limit)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"'limit' must be an integer, got {raw_limit!r}") from exc
        if limit < 1:
            raise ValueError("'limit' must be >= 1")
        limit = min(limit, self._max_limit)

        event_type = task.payload.get("event_type")
        if event_type is not None and not isinstance(event_type, str):
            raise ValueError("'event_type' must be a string when supplied")

        stmt = select(audit_log).order_by(desc(audit_log.c.id)).limit(limit)
        if event_type is not None:
            stmt = stmt.where(audit_log.c.event_type == event_type)

        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()

        events: list[dict[str, Any]] = [
            {
                "id": int(row["id"]),
                "ts": row["ts"].isoformat() if row["ts"] is not None else None,
                "event_type": row["event_type"],
                "payload": row["payload"],
            }
            for row in rows
        ]
        return {"events": events, "count": len(events)}
