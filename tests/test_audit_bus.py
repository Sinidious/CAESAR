from __future__ import annotations

import asyncio
import contextlib

import pytest

from caesar.praetor.audit_bus import AuditEventBus


async def test_publish_to_no_subscribers_is_noop() -> None:
    bus = AuditEventBus()
    bus.publish({"id": 1})  # must not raise
    assert bus.subscriber_count == 0


async def test_subscriber_receives_published_event() -> None:
    bus = AuditEventBus()
    received: list[dict[str, object]] = []

    async def consume() -> None:
        async for event in bus.subscribe():
            received.append(event)
            return

    task = asyncio.create_task(consume())
    # Wait briefly so subscribe() registers before publish.
    for _ in range(50):
        if bus.subscriber_count == 1:
            break
        await asyncio.sleep(0.005)
    bus.publish({"id": 7})
    await asyncio.wait_for(task, timeout=1.0)
    assert received == [{"id": 7}]


async def test_subscriber_unregisters_on_exit() -> None:
    bus = AuditEventBus()

    async def consume() -> None:
        async for _ in bus.subscribe():
            return

    task = asyncio.create_task(consume())
    for _ in range(50):
        if bus.subscriber_count == 1:
            break
        await asyncio.sleep(0.005)
    assert bus.subscriber_count == 1
    bus.publish({"id": 1})
    await asyncio.wait_for(task, timeout=1.0)
    # The async generator's finally runs via close(); give it a tick.
    for _ in range(50):
        if bus.subscriber_count == 0:
            break
        await asyncio.sleep(0.005)
    assert bus.subscriber_count == 0


async def test_drops_when_subscriber_lags(capsys: pytest.CaptureFixture[str]) -> None:
    """A subscriber that doesn't drain its queue must not block publish."""

    bus = AuditEventBus(queue_size=2)

    # Register a subscriber that never consumes.
    queue_holder: list[asyncio.Queue[dict[str, object]]] = []

    async def hold() -> None:
        async for _ in bus.subscribe():
            queue_holder.append(_)  # type: ignore[arg-type]
            await asyncio.sleep(10)  # block forever

    task = asyncio.create_task(hold())
    for _ in range(50):
        if bus.subscriber_count == 1:
            break
        await asyncio.sleep(0.005)
    # Fill queue + overflow.
    for i in range(10):
        bus.publish({"id": i})
    # Give the warning log a chance to flush.
    await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    out = capsys.readouterr().out
    assert "audit_bus.dropped" in out
