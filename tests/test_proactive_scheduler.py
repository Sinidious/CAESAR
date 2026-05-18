"""Tests for the proactive scheduler (ADR-0030)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.db.schema import audit_log
from caesar.proactive.scheduler import Scheduler
from caesar.proactive.triggers import ScheduleSource, Trigger


def _trigger(
    *,
    cron: str = "0 7 * * *",
    timezone: str = "UTC",
    enabled: bool = True,
    max_runtime: int = 300,
    trigger_id: str = "morning_brief",
    prompt: str = "good morning",
) -> Trigger:
    return Trigger(
        id=trigger_id,
        enabled=enabled,
        prompt=prompt,
        max_runtime_seconds=max_runtime,
        source=ScheduleSource(cron=cron, timezone=timezone),
    )


@pytest.fixture
def audit_clock() -> list[datetime]:
    """A mutable clock list-of-one so tests can advance time."""

    return [datetime(2026, 5, 17, 6, 0, 0, tzinfo=UTC)]


@pytest.fixture
def time_provider(audit_clock: list[datetime]):
    def _now() -> datetime:
        return audit_clock[0]

    return _now


@pytest.fixture
async def audit(engine: AsyncEngine) -> AsyncIterator[AuditLogger]:
    yield AuditLogger(engine, max_string_chars=4096)


async def _audit_events(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.begin() as conn:
        result = await conn.execute(select(audit_log.c.event_type, audit_log.c.payload))
        return [{"event_type": r.event_type, "payload": r.payload} for r in result]


# --- Arming ------------------------------------------------------------------


async def test_only_enabled_triggers_armed(time_provider) -> None:
    triggers = [
        _trigger(trigger_id="armed", enabled=True),
        _trigger(trigger_id="disarmed", enabled=False),
    ]

    async def cb(_t: Trigger) -> None:
        pass

    s = Scheduler(triggers, cb, time_provider=time_provider)
    assert s.armed_count == 1
    assert s.next_fire_for("armed") is not None
    assert s.next_fire_for("disarmed") is None


async def test_next_fire_for_unknown_id_returns_none(time_provider) -> None:
    async def cb(_t: Trigger) -> None:
        pass

    s = Scheduler([_trigger()], cb, time_provider=time_provider)
    assert s.next_fire_for("nope") is None


# --- announce() --------------------------------------------------------------


async def test_announce_emits_trigger_scheduled(
    time_provider, audit: AuditLogger, engine: AsyncEngine
) -> None:
    async def cb(_t: Trigger) -> None:
        pass

    triggers = [
        _trigger(trigger_id="armed", cron="0 7 * * *"),
        _trigger(trigger_id="disarmed", enabled=False, cron="0 8 * * *"),
    ]
    s = Scheduler(triggers, cb, audit=audit, time_provider=time_provider)
    await s.announce()

    events = await _audit_events(engine)
    scheduled = [e for e in events if e["event_type"] == "trigger.scheduled"]
    assert len(scheduled) == 1
    payload = scheduled[0]["payload"]
    assert payload["trigger_id"] == "armed"
    assert payload["cron"] == "0 7 * * *"
    assert payload["timezone"] == "UTC"
    assert "next_fire_at" in payload


async def test_announce_without_audit_is_silent(time_provider) -> None:
    async def cb(_t: Trigger) -> None:
        pass

    s = Scheduler([_trigger()], cb, time_provider=time_provider)
    await s.announce()  # must not raise


# --- tick() ------------------------------------------------------------------


async def test_tick_does_not_fire_before_due(time_provider, audit_clock: list[datetime]) -> None:
    fired: list[str] = []

    async def cb(t: Trigger) -> None:
        fired.append(t.id)

    s = Scheduler([_trigger(cron="0 7 * * *")], cb, time_provider=time_provider)
    audit_clock[0] = datetime(2026, 5, 17, 6, 59, 0, tzinfo=UTC)
    assert await s.tick() == 0
    assert fired == []


async def test_tick_fires_when_due_and_reschedules(
    time_provider, audit_clock: list[datetime], audit: AuditLogger, engine: AsyncEngine
) -> None:
    fired: list[str] = []

    async def cb(t: Trigger) -> None:
        fired.append(t.id)

    s = Scheduler([_trigger(cron="0 7 * * *")], cb, audit=audit, time_provider=time_provider)
    audit_clock[0] = datetime(2026, 5, 17, 7, 0, 1, tzinfo=UTC)
    assert await s.tick() == 1
    assert fired == ["morning_brief"]

    next_fire = s.next_fire_for("morning_brief")
    assert next_fire is not None
    assert next_fire > audit_clock[0]
    # Tomorrow at 7am UTC.
    assert next_fire == datetime(2026, 5, 18, 7, 0, 0, tzinfo=UTC)

    events = await _audit_events(engine)
    types = [e["event_type"] for e in events]
    assert "trigger.fired" in types
    assert "trigger.completed" in types


async def test_tick_audits_callback_error(
    time_provider, audit_clock: list[datetime], audit: AuditLogger, engine: AsyncEngine
) -> None:
    async def cb(_t: Trigger) -> None:
        raise RuntimeError("boom")

    s = Scheduler([_trigger()], cb, audit=audit, time_provider=time_provider)
    audit_clock[0] = datetime(2026, 5, 17, 7, 0, 1, tzinfo=UTC)
    assert await s.tick() == 1

    events = await _audit_events(engine)
    errors = [e for e in events if e["event_type"] == "trigger.error"]
    assert len(errors) == 1
    assert errors[0]["payload"]["error"] == "RuntimeError"
    assert errors[0]["payload"]["message"] == "boom"
    # No completed row when the callback raised.
    assert not any(e["event_type"] == "trigger.completed" for e in events)


async def test_tick_audits_callback_timeout(
    time_provider, audit_clock: list[datetime], audit: AuditLogger, engine: AsyncEngine
) -> None:
    async def cb(_t: Trigger) -> None:
        # asyncio.timeout(...) cancels this; the cancellation surfaces
        # as TimeoutError in the scheduler.
        await asyncio.sleep(10)

    s = Scheduler([_trigger(max_runtime=1)], cb, audit=audit, time_provider=time_provider)
    audit_clock[0] = datetime(2026, 5, 17, 7, 0, 1, tzinfo=UTC)
    assert await s.tick() == 1

    events = await _audit_events(engine)
    timeouts = [e for e in events if e["event_type"] == "trigger.timeout"]
    assert len(timeouts) == 1
    assert timeouts[0]["payload"]["max_runtime_seconds"] == 1


async def test_tick_fires_no_armed_returns_zero(time_provider) -> None:
    async def cb(_t: Trigger) -> None:
        pass

    s = Scheduler([], cb, time_provider=time_provider)
    assert await s.tick() == 0


async def test_tick_handles_multiple_due_at_once(
    time_provider, audit_clock: list[datetime]
) -> None:
    fired: list[str] = []

    async def cb(t: Trigger) -> None:
        fired.append(t.id)

    triggers = [
        _trigger(trigger_id="a", cron="0 7 * * *"),
        _trigger(trigger_id="b", cron="0 7 * * *"),
    ]
    s = Scheduler(triggers, cb, time_provider=time_provider)
    audit_clock[0] = datetime(2026, 5, 17, 7, 0, 1, tzinfo=UTC)
    assert await s.tick() == 2
    assert sorted(fired) == ["a", "b"]


# --- start()/stop() ----------------------------------------------------------


async def test_start_and_stop_cycle(
    time_provider, audit_clock: list[datetime], audit: AuditLogger
) -> None:
    fired: list[str] = []

    async def cb(t: Trigger) -> None:
        fired.append(t.id)

    # Use a frozen clock that's *just before* the fire time so the loop
    # parks on the wait_for and exits cleanly via stop().
    audit_clock[0] = datetime(2026, 5, 17, 6, 59, 0, tzinfo=UTC)
    s = Scheduler([_trigger()], cb, audit=audit, time_provider=time_provider)

    await s.start()
    # Idempotent: start while running is a no-op.
    await s.start()
    # Yield to let the task park on the wait_for.
    await asyncio.sleep(0)
    await s.stop()
    assert fired == []


async def test_stop_without_start_is_safe(time_provider) -> None:
    async def cb(_t: Trigger) -> None:
        pass

    s = Scheduler([_trigger()], cb, time_provider=time_provider)
    await s.stop()  # must not raise


async def test_run_exits_when_no_armed_triggers(time_provider) -> None:
    async def cb(_t: Trigger) -> None:
        pass

    s = Scheduler([], cb, time_provider=time_provider)
    await s.start()
    # With no armed triggers the loop returns immediately.
    if s._task is not None:
        await s._task
    await s.stop()


async def test_run_fires_due_trigger(
    audit_clock: list[datetime], time_provider, audit: AuditLogger
) -> None:
    """End-to-end through the asyncio loop without faking sleep."""

    fired: asyncio.Event = asyncio.Event()

    async def cb(_t: Trigger) -> None:
        fired.set()

    # Arm the trigger one second before its 07:00 slot, then advance the
    # clock past it. The scheduler's first _run iteration calls tick()
    # before sleeping, so the fire happens with no wall-clock wait.
    audit_clock[0] = datetime(2026, 5, 17, 6, 59, 59, tzinfo=UTC)
    s = Scheduler([_trigger()], cb, audit=audit, time_provider=time_provider)
    audit_clock[0] = datetime(2026, 5, 17, 7, 0, 1, tzinfo=UTC)
    await s.start()
    await asyncio.wait_for(fired.wait(), timeout=2.0)
    await s.stop()


async def test_run_continues_after_sleep_times_out(
    monkeypatch: pytest.MonkeyPatch,
    audit_clock: list[datetime],
    time_provider,
    audit: AuditLogger,
) -> None:
    """Exercise the wait_for + TimeoutError continue branch of _run.

    Shortens ``_MAX_SLEEP_SECONDS`` so the scheduler's wall-clock sleep
    times out fast; the test then advances the injected clock past the
    fire time so the next tick() actually fires.
    """

    from caesar.proactive import scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module, "_MAX_SLEEP_SECONDS", 0.05)

    fired: asyncio.Event = asyncio.Event()

    async def cb(_t: Trigger) -> None:
        fired.set()

    audit_clock[0] = datetime(2026, 5, 17, 6, 59, 59, tzinfo=UTC)
    s = Scheduler([_trigger()], cb, audit=audit, time_provider=time_provider)

    await s.start()
    # Let the first iteration park on wait_for and then time out.
    await asyncio.sleep(0.15)
    # Advance the clock; the next iteration's tick() will fire.
    audit_clock[0] = datetime(2026, 5, 17, 7, 0, 1, tzinfo=UTC)
    await asyncio.wait_for(fired.wait(), timeout=2.0)
    await s.stop()


async def test_default_time_provider_uses_wall_clock() -> None:
    """Constructing without ``time_provider`` arms triggers from real time."""

    async def cb(_t: Trigger) -> None:
        pass

    s = Scheduler([_trigger()], cb)
    assert s.armed_count == 1
    next_fire = s.next_fire_for("morning_brief")
    assert next_fire is not None
    # Whatever wall-clock time it is now, the next 07:00 UTC fire is
    # somewhere within the next 24 hours.
    assert next_fire > datetime.now(UTC)
    assert next_fire - datetime.now(UTC) <= timedelta(days=1)


# --- DST / timezone handling -------------------------------------------------


async def test_next_fire_uses_trigger_timezone() -> None:
    """A schedule in America/Los_Angeles fires at 7am local, not 7am UTC."""

    fired: list[Trigger] = []

    async def cb(t: Trigger) -> None:
        fired.append(t)

    # 14:00 UTC = 7:00 PDT on 2026-05-17 (PDT is UTC-7).
    clock = [datetime(2026, 5, 17, 13, 0, 0, tzinfo=UTC)]
    t = _trigger(cron="0 7 * * *", timezone="America/Los_Angeles")
    s = Scheduler([t], cb, time_provider=lambda: clock[0])

    nxt = s.next_fire_for(t.id)
    assert nxt is not None
    assert nxt == datetime(2026, 5, 17, 14, 0, 0, tzinfo=UTC)

    # Advance one second past the fire time.
    clock[0] = datetime(2026, 5, 17, 14, 0, 1, tzinfo=UTC)
    assert await s.tick() == 1

    # Next fire should be the next day at 7am PDT = 14:00 UTC.
    nxt2 = s.next_fire_for(t.id)
    assert nxt2 == datetime(2026, 5, 18, 14, 0, 0, tzinfo=UTC)
    # Sanity: the gap is approximately 24 hours (DST not crossing).
    assert nxt2 is not None
    assert nxt2 - datetime(2026, 5, 17, 14, 0, 0, tzinfo=UTC) == timedelta(days=1)
