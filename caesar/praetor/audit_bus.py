"""In-process pub/sub for live audit events (ADR-0021).

The dashboard's SSE stream subscribes here; :class:`AuditLogger`
publishes after each successful row write. Lagging subscribers drop
events rather than block the writer — the durable record is the
``audit_log`` table, the bus is best-effort live tap.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from caesar.log import get_logger


class AuditEventBus:
    """Fan-out async queue. One queue per subscriber."""

    def __init__(self, *, queue_size: int = 64) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._logger = get_logger("caesar.dashboard.audit_bus")

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: dict[str, Any]) -> None:
        """Non-blocking publish to every subscriber; drop on overflow."""

        dropped = 0
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dropped += 1
        if dropped:
            self._logger.warning("audit_bus.dropped", count=dropped)

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        """Yield events as they arrive. Releases the queue on exit."""

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)
