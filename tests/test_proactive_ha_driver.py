"""Tests for the HA event driver (ADR-0031, v1.6)."""

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
from caesar.proactive.ha_driver import HAEventDriver
from caesar.proactive.triggers import HASource, Trigger

# --- fakes ---------------------------------------------------------------


class _FakeStream:
    """Stand-in for ResilientHAEventStream.

    Records start/stop calls; tests drive the callback directly via
    ``deliver()`` so each test controls the event sequence without
    asyncio races.
    """

    def __init__(self) -> None:
        self.callback: Any = None
        self.event_type: str | None = None
        self.started = False
        self.stopped = False

    @classmethod
    def factory(cls, *args: Any, **kwargs: Any) -> _FakeStream:
        instance = cls()
        instance.callback = kwargs.get("callback") or (args[1] if len(args) > 1 else None)
        instance.event_type = kwargs.get("event_type")
        return instance

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def deliver(self, event: dict[str, Any]) -> None:
        assert self.callback is not None, "callback not wired"
        await self.callback(event)


class _RecordingRunner:
    """ProactiveRunner stand-in: records each fire() call."""

    def __init__(self) -> None:
        self.fired: list[Trigger] = []

    async def fire(self, trigger: Trigger) -> None:
        self.fired.append(trigger)


# --- fixtures ------------------------------------------------------------


@pytest.fixture
async def audit(engine: AsyncEngine) -> AsyncIterator[AuditLogger]:
    yield AuditLogger(engine, max_string_chars=4096)


async def _events_of_type(engine: AsyncEngine, event_type: str) -> list[dict[str, Any]]:
    async with engine.begin() as conn:
        result = await conn.execute(
            select(audit_log.c.event_type, audit_log.c.payload).where(
                audit_log.c.event_type == event_type
            )
        )
        return [{"event_type": r.event_type, "payload": r.payload} for r in result]


def _trigger(
    *,
    trigger_id: str = "late_office_motion",
    cooldown: int | None = None,
    enabled: bool = True,
    event_type: str = "state_changed",
    entity_id: str | None = "binary_sensor.office_motion",
    to: str | None = "on",
    time_window: str | None = None,
    timezone: str = "UTC",
    prompt: str = "motion: ping me",
) -> Trigger:
    return Trigger(
        id=trigger_id,
        enabled=enabled,
        prompt=prompt,
        cooldown_seconds=cooldown,
        source=HASource(
            event_type=event_type,
            entity_id=entity_id,
            to=to,
            time_window=time_window,
            timezone=timezone,
        ),
    )


def _build_driver(
    triggers: list[Trigger],
    audit_logger: AuditLogger,
    runner: _RecordingRunner,
    *,
    clock_value: list[datetime] | None = None,
) -> tuple[HAEventDriver, _FakeStream]:
    stream = _FakeStream()

    def clock() -> datetime:
        return (
            clock_value[0] if clock_value is not None else datetime(2026, 5, 17, 23, 0, tzinfo=UTC)
        )

    driver = HAEventDriver(
        triggers,
        ha=None,  # type: ignore[arg-type]
        runner=runner,  # type: ignore[arg-type]
        audit=audit_logger,
        clock=clock,
        stream=stream,  # type: ignore[arg-type]
    )
    stream.callback = driver._on_event
    return driver, stream


# --- arming + announce ---------------------------------------------------


async def test_only_enabled_ha_triggers_armed(audit: AuditLogger, engine: AsyncEngine) -> None:
    runner = _RecordingRunner()
    triggers = [
        _trigger(trigger_id="armed", enabled=True),
        _trigger(trigger_id="disarmed", enabled=False),
    ]
    driver, _stream = _build_driver(triggers, audit, runner)
    assert driver.armed_count == 1


async def test_schedule_triggers_are_ignored(audit: AuditLogger, engine: AsyncEngine) -> None:
    """HAEventDriver only arms HASource triggers; cron sources go to Scheduler."""

    from caesar.proactive.triggers import ScheduleSource

    runner = _RecordingRunner()
    cron_trigger = Trigger(
        id="cron_one",
        prompt="x",
        source=ScheduleSource(cron="0 7 * * *"),
    )
    driver, _stream = _build_driver(
        [cron_trigger, _trigger(trigger_id="armed")],
        audit,
        runner,
    )
    assert driver.armed_count == 1


async def test_announce_emits_trigger_subscribed(audit: AuditLogger, engine: AsyncEngine) -> None:
    runner = _RecordingRunner()
    driver, _stream = _build_driver([_trigger(cooldown=600)], audit, runner)
    await driver.start()
    subscribed = await _events_of_type(engine, "trigger.subscribed")
    assert len(subscribed) == 1
    payload = subscribed[0]["payload"]
    assert payload["trigger_id"] == "late_office_motion"
    assert payload["event_type"] == "state_changed"
    assert payload["entity_id"] == "binary_sensor.office_motion"
    assert payload["to"] == "on"
    assert payload["cooldown_seconds"] == 600
    await driver.stop()


async def test_start_starts_stream(audit: AuditLogger) -> None:
    runner = _RecordingRunner()
    driver, stream = _build_driver([_trigger()], audit, runner)
    await driver.start()
    assert stream.started
    await driver.stop()
    assert stream.stopped


def test_chosen_event_type_when_all_triggers_share_it(
    audit: AuditLogger,
) -> None:
    """Single event_type across triggers → subscribe to that one (cheaper)."""

    runner = _RecordingRunner()
    stream = _FakeStream()
    triggers = [
        _trigger(trigger_id="t1", entity_id="binary_sensor.a"),
        _trigger(trigger_id="t2", entity_id="binary_sensor.b"),
    ]
    driver = HAEventDriver(
        triggers,
        ha=None,  # type: ignore[arg-type]
        runner=runner,  # type: ignore[arg-type]
        audit=audit,
        stream=stream,  # type: ignore[arg-type]
    )
    assert driver._chosen_event_type() == "state_changed"


def test_chosen_event_type_none_when_triggers_mixed(
    audit: AuditLogger,
) -> None:
    """Multiple event_types → subscribe to all events, filter in-process."""

    runner = _RecordingRunner()
    stream = _FakeStream()
    triggers = [
        _trigger(trigger_id="state", event_type="state_changed"),
        Trigger(
            id="zwave",
            prompt="x",
            source=HASource(event_type="zwave_node_alive"),
        ),
    ]
    driver = HAEventDriver(
        triggers,
        ha=None,  # type: ignore[arg-type]
        runner=runner,  # type: ignore[arg-type]
        audit=audit,
        stream=stream,  # type: ignore[arg-type]
    )
    assert driver._chosen_event_type() is None


# --- matching + firing ---------------------------------------------------


async def test_matching_event_fires_runner(audit: AuditLogger) -> None:
    runner = _RecordingRunner()
    driver, stream = _build_driver([_trigger()], audit, runner)
    await driver.start()
    await stream.deliver(
        {
            "event_type": "state_changed",
            "data": {
                "entity_id": "binary_sensor.office_motion",
                "new_state": {"state": "on"},
            },
        }
    )
    assert [t.id for t in runner.fired] == ["late_office_motion"]
    await driver.stop()


async def test_non_matching_event_does_not_fire(audit: AuditLogger) -> None:
    runner = _RecordingRunner()
    driver, stream = _build_driver([_trigger()], audit, runner)
    await driver.start()
    await stream.deliver(
        {
            "event_type": "state_changed",
            "data": {
                "entity_id": "binary_sensor.kitchen_motion",
                "new_state": {"state": "on"},
            },
        }
    )
    assert runner.fired == []
    await driver.stop()


async def test_time_window_filter_uses_driver_clock(audit: AuditLogger) -> None:
    """Time-window matching uses the trigger's source.timezone via the driver clock."""

    runner = _RecordingRunner()
    # Clock: 14:00 UTC — outside the 22:00-06:00 UTC window.
    clock_value = [datetime(2026, 5, 17, 14, 0, tzinfo=UTC)]
    driver, stream = _build_driver(
        [_trigger(time_window="22:00-06:00")],
        audit,
        runner,
        clock_value=clock_value,
    )
    await driver.start()
    await stream.deliver(
        {
            "event_type": "state_changed",
            "data": {
                "entity_id": "binary_sensor.office_motion",
                "new_state": {"state": "on"},
            },
        }
    )
    assert runner.fired == []  # outside window
    # Move clock into the window.
    clock_value[0] = datetime(2026, 5, 17, 23, 0, tzinfo=UTC)
    await stream.deliver(
        {
            "event_type": "state_changed",
            "data": {
                "entity_id": "binary_sensor.office_motion",
                "new_state": {"state": "on"},
            },
        }
    )
    assert [t.id for t in runner.fired] == ["late_office_motion"]
    await driver.stop()


# --- cooldown + suppression ---------------------------------------------


async def test_cooldown_suppresses_followups(audit: AuditLogger, engine: AsyncEngine) -> None:
    runner = _RecordingRunner()
    clock_value = [datetime(2026, 5, 17, 23, 0, tzinfo=UTC)]
    driver, stream = _build_driver(
        [_trigger(cooldown=600)],
        audit,
        runner,
        clock_value=clock_value,
    )
    await driver.start()
    event = {
        "event_type": "state_changed",
        "data": {
            "entity_id": "binary_sensor.office_motion",
            "new_state": {"state": "on"},
        },
    }
    # First event fires.
    await stream.deliver(event)
    # Three follow-ups within the cooldown window — all suppressed.
    for delta_seconds in (10, 60, 120):
        clock_value[0] = datetime(2026, 5, 17, 23, 0, tzinfo=UTC) + timedelta(seconds=delta_seconds)
        await stream.deliver(event)
    assert len(runner.fired) == 1

    # stop() flushes pending suppressions into one coalesced row.
    await driver.stop()
    suppressed = await _events_of_type(engine, "trigger.suppressed")
    assert len(suppressed) == 1
    payload = suppressed[0]["payload"]
    assert payload["count"] == 3
    assert payload["trigger_id"] == "late_office_motion"


async def test_cooldown_window_elapses_allows_refire(
    audit: AuditLogger, engine: AsyncEngine
) -> None:
    runner = _RecordingRunner()
    clock_value = [datetime(2026, 5, 17, 23, 0, tzinfo=UTC)]
    driver, stream = _build_driver(
        [_trigger(cooldown=60)],
        audit,
        runner,
        clock_value=clock_value,
    )
    await driver.start()
    event = {
        "event_type": "state_changed",
        "data": {
            "entity_id": "binary_sensor.office_motion",
            "new_state": {"state": "on"},
        },
    }
    await stream.deliver(event)
    # Suppress one event mid-cooldown.
    clock_value[0] = datetime(2026, 5, 17, 23, 0, 30, tzinfo=UTC)
    await stream.deliver(event)
    # Past the cooldown — should refire AND flush the pending suppression.
    clock_value[0] = datetime(2026, 5, 17, 23, 1, 30, tzinfo=UTC)
    await stream.deliver(event)
    assert len(runner.fired) == 2

    suppressed = await _events_of_type(engine, "trigger.suppressed")
    # Suppression row was emitted as part of the refire flush, before stop().
    assert len(suppressed) == 1
    assert suppressed[0]["payload"]["count"] == 1
    await driver.stop()


async def test_no_cooldown_fires_every_match(audit: AuditLogger) -> None:
    runner = _RecordingRunner()
    driver, stream = _build_driver([_trigger(cooldown=None)], audit, runner)
    await driver.start()
    for _ in range(3):
        await stream.deliver(
            {
                "event_type": "state_changed",
                "data": {
                    "entity_id": "binary_sensor.office_motion",
                    "new_state": {"state": "on"},
                },
            }
        )
    assert len(runner.fired) == 3
    await driver.stop()


# --- isolation ----------------------------------------------------------


async def test_empty_driver_starts_and_stops_cleanly(audit: AuditLogger) -> None:
    """No armed triggers — driver wires nothing but lifecycle must work."""

    runner = _RecordingRunner()
    driver, stream = _build_driver([], audit, runner)
    await driver.start()
    await stream.deliver(  # event ignored — no triggers to match
        {"event_type": "state_changed", "data": {}}
    )
    assert runner.fired == []
    await driver.stop()


async def test_default_clock_uses_wall_clock(audit: AuditLogger) -> None:
    """Construction without a clock arg uses datetime.now(UTC)."""

    runner = _RecordingRunner()
    stream = _FakeStream()
    driver = HAEventDriver(
        [_trigger(time_window="00:00-23:59")],  # always-on window
        ha=None,  # type: ignore[arg-type]
        runner=runner,  # type: ignore[arg-type]
        audit=audit,
        stream=stream,  # type: ignore[arg-type]
    )
    stream.callback = driver._on_event
    await driver.start()
    await stream.deliver(
        {
            "event_type": "state_changed",
            "data": {
                "entity_id": "binary_sensor.office_motion",
                "new_state": {"state": "on"},
            },
        }
    )
    assert len(runner.fired) == 1
    await driver.stop()
    # Sanity: stream.stop was called, no asyncio races.
    assert stream.stopped


# Suppress unused warning when asyncio fixture is involved.
async def _silence_unused() -> None:  # pragma: no cover
    await asyncio.sleep(0)
