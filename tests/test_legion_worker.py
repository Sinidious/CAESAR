from __future__ import annotations

from typing import ClassVar

import pytest

from caesar.bus.client import Bus
from caesar.legion.protocol import TaskDispatch, dispatch_subject
from caesar.legion.worker import NoopWorker, Worker


def test_worker_requires_worker_id_on_subclass(bus: Bus) -> None:
    class Headless(Worker):
        capabilities: ClassVar[list[str]] = ["x"]

    with pytest.raises(ValueError, match="worker_id"):
        Headless(bus)


def test_worker_requires_capabilities_on_subclass(bus: Bus) -> None:
    class Empty(Worker):
        worker_id: ClassVar[str] = "empty"

    with pytest.raises(ValueError, match="capability"):
        Empty(bus)


async def test_noop_worker_responds(bus: Bus) -> None:
    worker = NoopWorker(bus)
    await worker.start()
    try:
        task = TaskDispatch(task_id="t1", capability="test.noop", payload={"hi": "there"})
        reply = await bus.request(
            dispatch_subject("noop"),
            task.model_dump_json().encode(),
            timeout=2.0,
        )
        from caesar.legion.protocol import TaskResult

        result = TaskResult.model_validate_json(reply)
        assert result.success is True
        assert result.result == {"echo": {"hi": "there"}, "capability": "test.noop"}
    finally:
        await worker.stop()


async def test_worker_handler_exceptions_become_error_results(bus: Bus) -> None:
    class Boom(Worker):
        worker_id: ClassVar[str] = "boom"
        capabilities: ClassVar[list[str]] = ["test.boom"]
        version: ClassVar[str] = "0.0.1"

        async def handle(self, task: TaskDispatch) -> dict[str, object]:
            raise RuntimeError("kapow")

    worker = Boom(bus)
    await worker.start()
    try:
        task = TaskDispatch(task_id="t-boom", capability="test.boom")
        from caesar.legion.protocol import TaskResult

        reply = await bus.request(
            dispatch_subject("boom"),
            task.model_dump_json().encode(),
            timeout=2.0,
        )
        result = TaskResult.model_validate_json(reply)
        assert result.success is False
        assert result.error == "kapow"
    finally:
        await worker.stop()


async def test_worker_rejects_invalid_dispatch_payload(bus: Bus) -> None:
    worker = NoopWorker(bus)
    await worker.start()
    try:
        from caesar.legion.protocol import TaskResult

        reply = await bus.request(
            dispatch_subject("noop"),
            b"{not even json",
            timeout=2.0,
        )
        result = TaskResult.model_validate_json(reply)
        assert result.success is False
        assert "invalid dispatch payload" in (result.error or "")
    finally:
        await worker.stop()


async def test_worker_stop_is_idempotent(bus: Bus) -> None:
    worker = NoopWorker(bus)
    await worker.start()
    await worker.stop()
    await worker.stop()  # no-op
