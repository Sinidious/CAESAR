"""Semantic memory (ADR-0010 amendment).

Two halves:

1. :class:`SemanticIndexer` — a background loop (mirroring the
   retention sweep) that polls for un-indexed ``audit_log`` rows
   matching configured event types, embeds their text, and stores
   the result in ``semantic_chunks``.
2. :func:`cosine_top_k` — Python-side similarity search. v0.4 keeps
   embeddings in a JSON column and ranks candidates in process. The
   schema is stable; a future milestone swaps the ranking path to a
   vector index without touching callers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.schema import audit_log, semantic_chunks
from caesar.llm.embeddings import Embedder
from caesar.log import get_logger


@dataclass(frozen=True)
class IndexResult:
    indexed: int


@dataclass(frozen=True)
class RecalledChunk:
    audit_log_id: int
    text: str
    distance: float
    payload: dict[str, Any]
    event_type: str


def _extract_text(event_type: str, payload: dict[str, Any]) -> str | None:
    """Return the text we want to embed for a given event, or None to skip."""

    if event_type == "chat.completed":
        reply = payload.get("reply")
        return reply if isinstance(reply, str) and reply.strip() else None
    # Fall back: stringify the payload so other event types are still indexable.
    return json.dumps(payload, default=str, sort_keys=True)


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Returns 0 if either side is the zero vector."""

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    return dot / denom if denom else 0.0


async def index_pending(
    engine: AsyncEngine,
    embedder: Embedder,
    *,
    event_types: list[str],
    batch_size: int = 32,
) -> IndexResult:
    """Embed and store un-indexed audit rows of the configured types."""

    indexed_ids_subq = select(semantic_chunks.c.audit_log_id).subquery()
    stmt = (
        select(audit_log)
        .where(audit_log.c.event_type.in_(event_types))
        .where(audit_log.c.id.not_in(select(indexed_ids_subq.c.audit_log_id)))
        .order_by(audit_log.c.id)
        .limit(batch_size)
    )

    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).mappings().all()

    pairs: list[tuple[int, str]] = []
    for row in rows:
        text = _extract_text(row["event_type"], row["payload"] or {})
        if text:
            pairs.append((int(row["id"]), text))

    if not pairs:
        return IndexResult(indexed=0)

    vectors = await embedder.embed([t for _, t in pairs])
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        for (audit_id, text), vec in zip(pairs, vectors, strict=True):
            await conn.execute(
                insert(semantic_chunks).values(
                    audit_log_id=audit_id,
                    text=text,
                    embedding=vec,
                    model=embedder.model,
                    created_at=now,
                )
            )
    return IndexResult(indexed=len(pairs))


async def cosine_top_k(
    engine: AsyncEngine,
    query_vector: list[float],
    *,
    limit: int,
) -> list[RecalledChunk]:
    """Rank every stored chunk by cosine similarity to ``query_vector``.

    Fetches all chunks and ranks in Python. Good enough for thousands
    of rows; the schema is stable so a future vector-index swap won't
    change the caller.
    """

    join = semantic_chunks.join(audit_log, semantic_chunks.c.audit_log_id == audit_log.c.id)
    stmt = select(
        semantic_chunks.c.audit_log_id,
        semantic_chunks.c.text,
        semantic_chunks.c.embedding,
        audit_log.c.payload,
        audit_log.c.event_type,
    ).select_from(join)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).mappings().all()

    scored: list[tuple[float, RecalledChunk]] = []
    for row in rows:
        vec = row["embedding"]
        if not isinstance(vec, list):
            continue
        similarity = _cosine(query_vector, vec)
        chunk = RecalledChunk(
            audit_log_id=int(row["audit_log_id"]),
            text=row["text"],
            distance=1.0 - similarity,
            payload=row["payload"] or {},
            event_type=row["event_type"],
        )
        scored.append((similarity, chunk))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [chunk for _, chunk in scored[:limit]]


class SemanticIndexer:
    """Background polling indexer for ``audit_log`` → ``semantic_chunks``."""

    def __init__(
        self,
        engine: AsyncEngine,
        embedder: Embedder,
        *,
        event_types: list[str],
        interval_seconds: float,
        batch_size: int = 32,
    ) -> None:
        self._engine = engine
        self._embedder = embedder
        self._event_types = list(event_types)
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None
        self._logger = get_logger("caesar.memory.semantic")

    @property
    def is_running(self) -> bool:
        return self._task is not None

    @property
    def event_types(self) -> list[str]:
        return list(self._event_types)

    async def index_once(self) -> IndexResult:
        return await index_pending(
            self._engine,
            self._embedder,
            event_types=self._event_types,
            batch_size=self._batch_size,
        )

    async def _loop(self) -> None:
        while True:
            try:
                result = await self.index_once()
                if result.indexed:
                    self._logger.info("semantic.index.batch", count=result.indexed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning("semantic.index.failed", error=str(exc))
            try:
                await asyncio.sleep(self._interval_seconds)
            except asyncio.CancelledError:
                raise

    def start_background(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="memory.semantic.indexer")
        self._logger.info(
            "semantic.index.started",
            event_types=self._event_types,
            interval_seconds=self._interval_seconds,
        )

    async def stop_background(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._logger.info("semantic.index.stopped")
