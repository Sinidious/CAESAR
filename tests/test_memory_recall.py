from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.bus.client import Bus
from caesar.db.schema import audit_log
from caesar.legion.memory_recall import CAPABILITY, MemoryRecallWorker
from caesar.legion.protocol import TaskDispatch


async def _seed(engine: AsyncEngine, rows: list[dict[str, object]]) -> None:
    async with engine.begin() as conn:
        for row in rows:
            await conn.execute(insert(audit_log).values(**row))


def _row(event_type: str = "chat.completed", **payload: object) -> dict[str, object]:
    return {
        "ts": datetime.now(UTC),
        "event_type": event_type,
        "payload": payload or {"k": "v"},
    }


def _unbound_worker(
    engine: AsyncEngine,
    *,
    default_limit: int = 5,
    max_limit: int = 10,
) -> MemoryRecallWorker:
    """Construct against an unconnected Bus; handle() never touches it."""

    return MemoryRecallWorker(
        Bus("nats://unit-test"),
        engine,
        default_limit=default_limit,
        max_limit=max_limit,
    )


def _events(out: dict[str, object]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], out["events"])


async def test_returns_newest_first(engine: AsyncEngine) -> None:
    await _seed(
        engine,
        [_row(k=i) for i in range(3)],
    )
    worker = _unbound_worker(engine)
    task = TaskDispatch(task_id="t", capability=CAPABILITY)
    out = await worker.handle(task)
    assert out["count"] == 3
    payloads = [e["payload"] for e in _events(out)]
    assert [p["k"] for p in payloads] == [2, 1, 0]


async def test_respects_limit(engine: AsyncEngine) -> None:
    await _seed(engine, [_row(k=i) for i in range(5)])
    worker = _unbound_worker(engine)
    task = TaskDispatch(task_id="t", capability=CAPABILITY, payload={"limit": 2})
    out = await worker.handle(task)
    assert out["count"] == 2


async def test_caps_limit_at_max(engine: AsyncEngine) -> None:
    await _seed(engine, [_row(k=i) for i in range(20)])
    worker = _unbound_worker(engine, max_limit=3)
    out = await worker.handle(
        TaskDispatch(task_id="t", capability=CAPABILITY, payload={"limit": 50})
    )
    assert out["count"] == 3


async def test_filters_by_event_type(engine: AsyncEngine) -> None:
    await _seed(
        engine,
        [
            _row("chat.completed", k=1),
            _row("service.called", k=2),
            _row("chat.completed", k=3),
        ],
    )
    worker = _unbound_worker(engine)
    out = await worker.handle(
        TaskDispatch(
            task_id="t",
            capability=CAPABILITY,
            payload={"event_type": "service.called"},
        )
    )
    assert out["count"] == 1
    assert _events(out)[0]["payload"]["k"] == 2


async def test_rejects_invalid_limit(engine: AsyncEngine) -> None:
    worker = _unbound_worker(engine)
    with pytest.raises(ValueError, match="must be an integer"):
        await worker.handle(
            TaskDispatch(task_id="t", capability=CAPABILITY, payload={"limit": "lots"})
        )


async def test_rejects_negative_limit(engine: AsyncEngine) -> None:
    worker = _unbound_worker(engine)
    with pytest.raises(ValueError, match=">= 1"):
        await worker.handle(TaskDispatch(task_id="t", capability=CAPABILITY, payload={"limit": 0}))


async def test_rejects_non_string_event_type(engine: AsyncEngine) -> None:
    worker = _unbound_worker(engine)
    with pytest.raises(ValueError, match="must be a string"):
        await worker.handle(
            TaskDispatch(
                task_id="t",
                capability=CAPABILITY,
                payload={"event_type": 42},
            )
        )


async def test_e2e_via_bus_and_registry(bus: Bus, registry, engine: AsyncEngine) -> None:
    """The worker registers, receives a dispatch over NATS, returns events."""

    import asyncio

    await _seed(engine, [_row(k="hello")])
    worker = MemoryRecallWorker(bus, engine)
    await worker.start()
    try:
        # Wait for registration to propagate.
        for _ in range(50):
            if CAPABILITY in set(registry.capabilities()):
                break
            await asyncio.sleep(0.02)
        result = await registry.dispatch(CAPABILITY, {"limit": 5})
        assert result.success is True
        assert (result.result or {})["count"] == 1
        assert (result.result or {})["events"][0]["payload"]["k"] == "hello"
    finally:
        await worker.stop()
