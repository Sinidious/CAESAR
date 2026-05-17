from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.bus.client import Bus
from caesar.db.schema import audit_log
from caesar.legion.protocol import TaskDispatch
from caesar.legion.semantic_recall import CAPABILITY, SemanticRecallWorker
from caesar.llm.embeddings import StubEmbedder
from caesar.memory.semantic import index_pending


async def _seed_and_index(engine: AsyncEngine, embedder: StubEmbedder, replies: list[str]) -> None:
    async with engine.begin() as conn:
        for r in replies:
            await conn.execute(
                insert(audit_log).values(
                    ts=datetime.now(UTC),
                    event_type="chat.completed",
                    payload={"reply": r},
                )
            )
    await index_pending(engine, embedder, event_types=["chat.completed"])


def _unbound_worker(engine: AsyncEngine, embedder: StubEmbedder) -> SemanticRecallWorker:
    return SemanticRecallWorker(
        Bus("nats://unit-test"), engine, embedder, default_limit=3, max_limit=10
    )


async def test_handle_returns_top_match(engine: AsyncEngine) -> None:
    embedder = StubEmbedder(dimension=32)
    await _seed_and_index(engine, embedder, ["kitchen light", "garage door"])
    worker = _unbound_worker(engine, embedder)
    out = await worker.handle(
        TaskDispatch(task_id="t", capability=CAPABILITY, payload={"query": "kitchen light"})
    )
    assert out["count"] == 2
    assert isinstance(out["results"], list)
    results = out["results"]
    assert isinstance(results, list)
    first: dict[str, Any] = results[0]
    assert first["text"] == "kitchen light"


async def test_handle_respects_limit(engine: AsyncEngine) -> None:
    embedder = StubEmbedder(dimension=32)
    await _seed_and_index(engine, embedder, [f"r{i}" for i in range(5)])
    worker = _unbound_worker(engine, embedder)
    out = await worker.handle(
        TaskDispatch(
            task_id="t",
            capability=CAPABILITY,
            payload={"query": "anything", "limit": 2},
        )
    )
    assert out["count"] == 2


async def test_handle_caps_limit_at_max(engine: AsyncEngine) -> None:
    embedder = StubEmbedder(dimension=32)
    await _seed_and_index(engine, embedder, [f"r{i}" for i in range(20)])
    worker = SemanticRecallWorker(
        Bus("nats://unit-test"), engine, embedder, default_limit=3, max_limit=4
    )
    out = await worker.handle(
        TaskDispatch(task_id="t", capability=CAPABILITY, payload={"query": "x", "limit": 100})
    )
    assert out["count"] == 4


async def test_handle_rejects_empty_query(engine: AsyncEngine) -> None:
    worker = _unbound_worker(engine, StubEmbedder(dimension=32))
    with pytest.raises(ValueError, match="non-empty string"):
        await worker.handle(
            TaskDispatch(task_id="t", capability=CAPABILITY, payload={"query": "  "})
        )


async def test_handle_rejects_missing_query(engine: AsyncEngine) -> None:
    worker = _unbound_worker(engine, StubEmbedder(dimension=32))
    with pytest.raises(ValueError, match="non-empty string"):
        await worker.handle(TaskDispatch(task_id="t", capability=CAPABILITY, payload={}))


async def test_handle_rejects_invalid_limit(engine: AsyncEngine) -> None:
    worker = _unbound_worker(engine, StubEmbedder(dimension=32))
    with pytest.raises(ValueError, match="must be an integer"):
        await worker.handle(
            TaskDispatch(
                task_id="t", capability=CAPABILITY, payload={"query": "x", "limit": "lots"}
            )
        )


async def test_handle_rejects_zero_limit(engine: AsyncEngine) -> None:
    worker = _unbound_worker(engine, StubEmbedder(dimension=32))
    with pytest.raises(ValueError, match=">= 1"):
        await worker.handle(
            TaskDispatch(task_id="t", capability=CAPABILITY, payload={"query": "x", "limit": 0})
        )


async def test_e2e_via_bus_and_registry(bus: Bus, registry, engine: AsyncEngine) -> None:
    embedder = StubEmbedder(dimension=32)
    await _seed_and_index(engine, embedder, ["the kitchen light is on"])
    worker = SemanticRecallWorker(bus, engine, embedder)
    await worker.start()
    try:
        for _ in range(50):
            if CAPABILITY in set(registry.capabilities()):
                break
            await asyncio.sleep(0.02)
        result = await registry.dispatch(CAPABILITY, {"query": "kitchen light"})
        assert result.success is True
        assert (result.result or {})["count"] == 1
    finally:
        await worker.stop()
