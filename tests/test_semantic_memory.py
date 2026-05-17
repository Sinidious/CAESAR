from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.schema import audit_log, semantic_chunks
from caesar.llm.embeddings import StubEmbedder
from caesar.memory.semantic import (
    SemanticIndexer,
    _cosine,
    _extract_text,
    cosine_top_k,
    index_pending,
)


async def _insert_event(engine: AsyncEngine, *, event_type: str, payload: dict[str, object]) -> int:
    async with engine.begin() as conn:
        result = await conn.execute(
            insert(audit_log).values(ts=datetime.now(UTC), event_type=event_type, payload=payload)
        )
    pk = result.inserted_primary_key
    assert pk is not None
    return int(pk[0])


# --- _extract_text -----------------------------------------------------------


def test_extract_text_uses_chat_reply() -> None:
    out = _extract_text("chat.completed", {"reply": "kitchen light is on"})
    assert out == "kitchen light is on"


def test_extract_text_skips_empty_chat_reply() -> None:
    assert _extract_text("chat.completed", {"reply": "   "}) is None
    assert _extract_text("chat.completed", {"reply": ""}) is None
    assert _extract_text("chat.completed", {}) is None


def test_extract_text_falls_back_to_json_for_other_events() -> None:
    out = _extract_text("service.called", {"domain": "light", "service": "turn_on"})
    assert out is not None
    assert "light" in out and "turn_on" in out


# --- _cosine -----------------------------------------------------------------


def test_cosine_identity() -> None:
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_orthogonal() -> None:
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector_safe() -> None:
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


# --- index_pending -----------------------------------------------------------


async def test_index_pending_indexes_only_target_event_types(
    engine: AsyncEngine,
) -> None:
    await _insert_event(engine, event_type="chat.completed", payload={"reply": "a"})
    await _insert_event(engine, event_type="service.called", payload={"x": 1})
    await _insert_event(engine, event_type="chat.completed", payload={"reply": "b"})

    embedder = StubEmbedder(dimension=32)
    result = await index_pending(engine, embedder, event_types=["chat.completed"])

    assert result.indexed == 2
    async with engine.connect() as conn:
        count = (await conn.execute(select(func.count()).select_from(semantic_chunks))).scalar_one()
    assert count == 2


async def test_index_pending_is_idempotent(engine: AsyncEngine) -> None:
    await _insert_event(engine, event_type="chat.completed", payload={"reply": "a"})
    embedder = StubEmbedder(dimension=32)

    first = await index_pending(engine, embedder, event_types=["chat.completed"])
    second = await index_pending(engine, embedder, event_types=["chat.completed"])

    assert first.indexed == 1
    assert second.indexed == 0


async def test_index_pending_skips_empty_payload(engine: AsyncEngine) -> None:
    await _insert_event(engine, event_type="chat.completed", payload={"reply": ""})
    embedder = StubEmbedder(dimension=32)
    result = await index_pending(engine, embedder, event_types=["chat.completed"])
    assert result.indexed == 0


async def test_index_pending_empty_when_no_matching_rows(
    engine: AsyncEngine,
) -> None:
    embedder = StubEmbedder(dimension=32)
    result = await index_pending(engine, embedder, event_types=["chat.completed"])
    assert result.indexed == 0


# --- cosine_top_k ------------------------------------------------------------


async def test_cosine_top_k_returns_best_match(engine: AsyncEngine) -> None:
    embedder = StubEmbedder(dimension=32)
    await _insert_event(engine, event_type="chat.completed", payload={"reply": "kitchen lights"})
    await _insert_event(engine, event_type="chat.completed", payload={"reply": "garage door"})
    await index_pending(engine, embedder, event_types=["chat.completed"])

    query = (await embedder.embed(["kitchen lights"]))[0]
    chunks = await cosine_top_k(engine, query, limit=1)
    assert len(chunks) == 1
    assert chunks[0].text == "kitchen lights"
    assert chunks[0].distance == pytest.approx(0.0, abs=1e-6)
    assert chunks[0].event_type == "chat.completed"


async def test_cosine_top_k_respects_limit(engine: AsyncEngine) -> None:
    embedder = StubEmbedder(dimension=32)
    for i in range(5):
        await _insert_event(engine, event_type="chat.completed", payload={"reply": f"r{i}"})
    await index_pending(engine, embedder, event_types=["chat.completed"])
    query = (await embedder.embed(["anything"]))[0]
    chunks = await cosine_top_k(engine, query, limit=3)
    assert len(chunks) == 3


# --- SemanticIndexer background loop ----------------------------------------


async def test_indexer_lifecycle_idempotent(engine: AsyncEngine) -> None:
    indexer = SemanticIndexer(
        engine,
        StubEmbedder(dimension=32),
        event_types=["chat.completed"],
        interval_seconds=60,
    )
    indexer.start_background()
    indexer.start_background()  # idempotent
    assert indexer.is_running
    assert indexer.event_types == ["chat.completed"]

    await indexer.stop_background()
    await indexer.stop_background()  # idempotent
    assert not indexer.is_running


async def test_indexer_background_loop_indexes_pending_rows(
    engine: AsyncEngine,
) -> None:
    await _insert_event(engine, event_type="chat.completed", payload={"reply": "hi"})
    indexer = SemanticIndexer(
        engine,
        StubEmbedder(dimension=32),
        event_types=["chat.completed"],
        interval_seconds=0.05,
    )
    indexer.start_background()
    indexed = False
    try:
        for _ in range(100):
            async with engine.connect() as conn:
                count = (
                    await conn.execute(select(func.count()).select_from(semantic_chunks))
                ).scalar_one()
            if count >= 1:
                indexed = True
                break
            await asyncio.sleep(0.05)
    finally:
        await indexer.stop_background()
    assert indexed, "background indexer did not write a chunk"


async def test_indexer_loop_survives_embed_failure(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = StubEmbedder(dimension=32)
    indexer = SemanticIndexer(
        engine,
        embedder,
        event_types=["chat.completed"],
        interval_seconds=0.05,
    )

    calls = 0
    real_once = indexer.index_once

    async def flaky() -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return await real_once()

    monkeypatch.setattr(indexer, "index_once", flaky)
    indexer.start_background()
    try:
        for _ in range(100):
            if calls >= 2:
                break
            await asyncio.sleep(0.02)
        assert calls >= 2
    finally:
        await indexer.stop_background()
