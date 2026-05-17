from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from caesar.bus.client import Bus
from caesar.legion.protocol import REGISTRATION_SUBJECT, WorkerRegistration
from caesar.legion.registry import NoWorkerAvailableError, WorkerRegistry
from caesar.legion.worker import NoopWorker


async def _wait_for(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("predicate not satisfied within timeout")


async def test_registers_worker(bus: Bus, registry: WorkerRegistry) -> None:
    worker = NoopWorker(bus)
    await worker.start()
    try:
        await _wait_for(lambda: "noop" in registry.workers)
        assert registry.workers["noop"].capabilities == ["test.noop"]
    finally:
        await worker.stop()


async def test_dispatch_round_trip(bus: Bus, registry: WorkerRegistry) -> None:
    worker = NoopWorker(bus)
    await worker.start()
    try:
        await _wait_for(lambda: "noop" in registry.workers)
        result = await registry.dispatch("test.noop", {"x": 1})
        assert result.success is True
        assert result.result == {"echo": {"x": 1}, "capability": "test.noop"}
        assert result.worker_id == "noop"
    finally:
        await worker.stop()


async def test_dispatch_uses_default_payload(bus: Bus, registry: WorkerRegistry) -> None:
    worker = NoopWorker(bus)
    await worker.start()
    try:
        await _wait_for(lambda: "noop" in registry.workers)
        result = await registry.dispatch("test.noop")
        assert result.result == {"echo": {}, "capability": "test.noop"}
    finally:
        await worker.stop()


async def test_dispatch_unknown_capability_raises(
    registry: WorkerRegistry,
) -> None:
    with pytest.raises(NoWorkerAvailableError):
        await registry.dispatch("test.unknown")


async def test_round_robin_across_two_workers(bus: Bus, registry: WorkerRegistry) -> None:
    from typing import ClassVar

    class W1(NoopWorker):
        worker_id: ClassVar[str] = "noop-1"

    class W2(NoopWorker):
        worker_id: ClassVar[str] = "noop-2"

    w1, w2 = W1(bus), W2(bus)
    await w1.start()
    await w2.start()
    try:
        await _wait_for(lambda: {"noop-1", "noop-2"}.issubset(registry.workers.keys()))
        ids = {(await registry.dispatch("test.noop")).worker_id for _ in range(4)}
        # Both workers picked at least once.
        assert ids == {"noop-1", "noop-2"}
    finally:
        await w1.stop()
        await w2.stop()


async def test_capabilities_view(bus: Bus, registry: WorkerRegistry) -> None:
    worker = NoopWorker(bus)
    await worker.start()
    try:
        await _wait_for(lambda: "noop" in registry.workers)
        assert "test.noop" in set(registry.capabilities())
    finally:
        await worker.stop()


async def test_find_returns_matching_workers(bus: Bus, registry: WorkerRegistry) -> None:
    worker = NoopWorker(bus)
    await worker.start()
    try:
        await _wait_for(lambda: "noop" in registry.workers)
        matches = registry.find("test.noop")
        assert [w.worker_id for w in matches] == ["noop"]
        assert registry.find("nonexistent.capability") == []
    finally:
        await worker.stop()


async def test_start_idempotent(bus: Bus) -> None:
    r = WorkerRegistry(bus)
    await r.start()
    await r.start()  # no-op
    await r.stop()


async def test_stop_idempotent(bus: Bus) -> None:
    r = WorkerRegistry(bus)
    await r.start()
    await r.stop()
    await r.stop()  # no-op


async def test_bad_registration_is_ignored(bus: Bus, registry: WorkerRegistry) -> None:
    """A malformed registration message must not crash the registry."""

    await bus.publish(REGISTRATION_SUBJECT, b"{not json")
    # Send a valid registration after.
    valid = WorkerRegistration(worker_id="valid", capabilities=["c"], version="0.0.1")
    await bus.publish(REGISTRATION_SUBJECT, valid.model_dump_json().encode())
    await _wait_for(lambda: "valid" in registry.workers)
