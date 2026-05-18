"""Tests for the resilient HA event subscription (ADR-0031, v1.6)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.db.schema import audit_log
from caesar.ha.subscription import ResilientHAEventStream


class _FakeHA:
    """HAClient stand-in that scripts subscribe_events generations.

    ``script`` is a list of lists. Each outer entry is one subscription
    lifetime — the generator yields each event in the inner list, then
    ends. The next subscribe_events call uses the next inner list.

    A list with a single ``Exception`` element raises that exception
    instead of yielding (simulating a connection error).
    """

    def __init__(self, script: list[list[Any]]) -> None:
        self._script = list(script)
        self.subscribe_calls: list[str | None] = []

    async def subscribe_events(
        self, event_type: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        self.subscribe_calls.append(event_type)
        try:
            batch = self._script.pop(0)
        except IndexError:
            # No more scripted lifetimes; block forever so the stream
            # parks here until the test calls stop().
            await asyncio.Event().wait()
            return  # pragma: no cover - unreachable
        for item in batch:
            if isinstance(item, Exception):
                raise item
            yield item


async def _no_sleep(_seconds: float) -> None:
    """Replacement for asyncio.sleep that yields once and returns."""

    await asyncio.sleep(0)


async def _events_seen(stream_callback: Any) -> list[dict[str, Any]]:
    """Convenience: pull events received by the recording callback."""

    return list(stream_callback.events)


@pytest.fixture
def event_collector():
    class Collector:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []

        async def __call__(self, event: dict[str, Any]) -> None:
            self.events.append(event)

    return Collector()


@pytest.fixture
async def audit(engine: AsyncEngine) -> AsyncIterator[AuditLogger]:
    yield AuditLogger(engine, max_string_chars=4096)


async def _audit_events(engine: AsyncEngine, event_type: str) -> list[dict[str, Any]]:
    async with engine.begin() as conn:
        result = await conn.execute(
            select(audit_log.c.event_type, audit_log.c.payload).where(
                audit_log.c.event_type == event_type
            )
        )
        return [{"event_type": r.event_type, "payload": r.payload} for r in result]


# --- Happy path -----------------------------------------------------------


async def test_stream_delivers_events_to_callback(event_collector) -> None:
    ha = _FakeHA(
        [
            [
                {"event_type": "state_changed", "data": {"entity_id": "light.x"}},
                {"event_type": "state_changed", "data": {"entity_id": "light.y"}},
            ],
        ]
    )
    stream = ResilientHAEventStream(
        ha,  # type: ignore[arg-type]
        event_collector,
        sleep=_no_sleep,
    )
    await stream.start()
    # Generator ends → stream loops; let the loop iterate a few times.
    await asyncio.sleep(0.05)
    await stream.stop()
    assert [e["data"]["entity_id"] for e in event_collector.events] == [
        "light.x",
        "light.y",
    ]


async def test_stream_passes_event_type_filter(event_collector) -> None:
    ha = _FakeHA([[]])
    stream = ResilientHAEventStream(
        ha,  # type: ignore[arg-type]
        event_collector,
        event_type="state_changed",
        sleep=_no_sleep,
    )
    await stream.start()
    await asyncio.sleep(0.05)
    await stream.stop()
    assert ha.subscribe_calls[0] == "state_changed"


# --- Audit logging --------------------------------------------------------


async def test_first_event_emits_subscription_opened(
    event_collector, audit: AuditLogger, engine: AsyncEngine
) -> None:
    ha = _FakeHA(
        [
            [{"event_type": "state_changed", "data": {}}],
        ]
    )
    stream = ResilientHAEventStream(
        ha,  # type: ignore[arg-type]
        event_collector,
        audit=audit,
        sleep=_no_sleep,
    )
    await stream.start()
    await asyncio.sleep(0.05)
    await stream.stop()

    opened = await _audit_events(engine, "ha.subscription.opened")
    assert len(opened) == 1


async def test_subsequent_connect_emits_reconnected(
    event_collector, audit: AuditLogger, engine: AsyncEngine
) -> None:
    """Two scripted lifetimes → one opened + one reconnected."""

    ha = _FakeHA(
        [
            [{"event_type": "state_changed", "data": {"i": 1}}],
            [{"event_type": "state_changed", "data": {"i": 2}}],
        ]
    )
    stream = ResilientHAEventStream(
        ha,  # type: ignore[arg-type]
        event_collector,
        audit=audit,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.01,
        sleep=_no_sleep,
    )
    await stream.start()
    # Let both lifetimes play through (initial connect + tiny backoff + reconnect).
    for _ in range(60):
        await asyncio.sleep(0.01)
        if len(event_collector.events) >= 2:
            break
    await stream.stop()

    opened = await _audit_events(engine, "ha.subscription.opened")
    reconnected = await _audit_events(engine, "ha.subscription.reconnected")
    assert len(opened) == 1
    assert len(reconnected) >= 1
    assert reconnected[0]["payload"]["connect_count"] >= 2


async def test_stop_emits_subscription_closed(
    event_collector, audit: AuditLogger, engine: AsyncEngine
) -> None:
    ha = _FakeHA([[{"event_type": "state_changed", "data": {}}]])
    stream = ResilientHAEventStream(
        ha,  # type: ignore[arg-type]
        event_collector,
        audit=audit,
        sleep=_no_sleep,
    )
    await stream.start()
    await asyncio.sleep(0.05)
    await stream.stop()

    closed = await _audit_events(engine, "ha.subscription.closed")
    assert len(closed) == 1
    assert closed[0]["payload"]["reason"] == "stopped"


# --- Reconnect on error ---------------------------------------------------


async def test_stream_recovers_from_connection_error(
    event_collector, audit: AuditLogger, engine: AsyncEngine
) -> None:
    """First lifetime raises; second yields one event."""

    ha = _FakeHA(
        [
            [ConnectionError("HA went away")],
            [{"event_type": "state_changed", "data": {"recovered": True}}],
        ]
    )
    stream = ResilientHAEventStream(
        ha,  # type: ignore[arg-type]
        event_collector,
        audit=audit,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.01,
        sleep=_no_sleep,
    )
    await stream.start()
    for _ in range(40):
        await asyncio.sleep(0.01)
        if event_collector.events:
            break
    await stream.stop()

    assert event_collector.events == [{"event_type": "state_changed", "data": {"recovered": True}}]
    # Audit shape: opened once (recovery is the FIRST successful connect
    # because the prior attempt failed before yielding any event).
    opened = await _audit_events(engine, "ha.subscription.opened")
    assert len(opened) == 1


# --- Callback failures ----------------------------------------------------


async def test_callback_exception_does_not_kill_stream(
    audit: AuditLogger, engine: AsyncEngine
) -> None:
    seen: list[dict[str, Any]] = []

    async def boom_then_record(event: dict[str, Any]) -> None:
        if not seen:
            seen.append(event)
            raise RuntimeError("first call fails")
        seen.append(event)

    ha = _FakeHA(
        [
            [
                {"event_type": "state_changed", "data": {"i": 1}},
                {"event_type": "state_changed", "data": {"i": 2}},
            ]
        ]
    )
    stream = ResilientHAEventStream(
        ha,  # type: ignore[arg-type]
        boom_then_record,
        audit=audit,
        sleep=_no_sleep,
    )
    await stream.start()
    await asyncio.sleep(0.1)
    await stream.stop()

    assert [e["data"]["i"] for e in seen] == [1, 2]
    errors = await _audit_events(engine, "ha.subscription.callback_error")
    assert len(errors) == 1
    assert errors[0]["payload"]["error"] == "RuntimeError"


# --- Lifecycle ------------------------------------------------------------


async def test_start_is_idempotent(event_collector) -> None:
    ha = _FakeHA([[{"event_type": "state_changed", "data": {}}]])
    stream = ResilientHAEventStream(
        ha,  # type: ignore[arg-type]
        event_collector,
        sleep=_no_sleep,
    )
    await stream.start()
    await stream.start()  # no-op
    await asyncio.sleep(0)
    await stream.stop()


async def test_connect_count_reflects_each_lifetime(event_collector, audit: AuditLogger) -> None:
    """Each successful connect bumps the count exposed to callers."""

    ha = _FakeHA(
        [
            [{"event_type": "state_changed", "data": {"i": 1}}],
            [{"event_type": "state_changed", "data": {"i": 2}}],
        ]
    )
    stream = ResilientHAEventStream(
        ha,  # type: ignore[arg-type]
        event_collector,
        audit=audit,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.01,
    )
    await stream.start()
    for _ in range(60):
        await asyncio.sleep(0.01)
        if stream.connect_count >= 2:
            break
    await stream.stop()
    assert stream.connect_count >= 2


async def test_stop_without_start_is_safe(event_collector) -> None:
    ha = _FakeHA([[]])
    stream = ResilientHAEventStream(
        ha,  # type: ignore[arg-type]
        event_collector,
        sleep=_no_sleep,
    )
    await stream.stop()  # must not raise
