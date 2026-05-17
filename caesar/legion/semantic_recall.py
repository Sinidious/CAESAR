"""Semantic-recall worker (ADR-0010 amendment).

Capability ``memory.semantic_recall``. Takes a natural-language
``query``, embeds it with the configured :class:`Embedder`, and
returns the top-k previously-indexed chunks ranked by cosine
similarity.

Input payload:

.. code-block:: json

    {
        "query": "what did we say about the kitchen light?",
        "limit": 5
    }

Output:

.. code-block:: json

    {
        "results": [
            {
                "audit_log_id": 42,
                "event_type": "chat.completed",
                "text": "I turned on the kitchen light.",
                "distance": 0.21,
                "payload": {...}
            },
            ...
        ]
    }
"""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.bus.client import Bus
from caesar.legion.protocol import TaskDispatch
from caesar.legion.worker import Worker
from caesar.llm.embeddings import Embedder
from caesar.memory.semantic import cosine_top_k

CAPABILITY = "memory.semantic_recall"
WORKER_ID = "semantic_recall"


class SemanticRecallWorker(Worker):
    """Embed a query and return top-k similar audit chunks."""

    worker_id: ClassVar[str] = WORKER_ID
    capabilities: ClassVar[list[str]] = [CAPABILITY]
    version: ClassVar[str] = "0.1.3"

    def __init__(
        self,
        bus: Bus,
        engine: AsyncEngine,
        embedder: Embedder,
        *,
        default_limit: int = 5,
        max_limit: int = 50,
    ) -> None:
        super().__init__(bus)
        self._engine = engine
        self._embedder = embedder
        self._default_limit = default_limit
        self._max_limit = max_limit

    async def handle(self, task: TaskDispatch) -> dict[str, object]:
        query = task.payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("'query' must be a non-empty string")

        raw_limit = task.payload.get("limit", self._default_limit)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"'limit' must be an integer, got {raw_limit!r}") from exc
        if limit < 1:
            raise ValueError("'limit' must be >= 1")
        limit = min(limit, self._max_limit)

        embeddings = await self._embedder.embed([query])
        query_vec = embeddings[0]
        chunks = await cosine_top_k(self._engine, query_vec, limit=limit)

        results: list[dict[str, Any]] = [
            {
                "audit_log_id": chunk.audit_log_id,
                "event_type": chunk.event_type,
                "text": chunk.text,
                "distance": chunk.distance,
                "payload": chunk.payload,
            }
            for chunk in chunks
        ]
        return {"results": results, "count": len(results)}
