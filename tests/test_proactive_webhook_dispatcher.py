"""Tests for the webhook dispatcher (ADR-0032, v1.7)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.db.schema import audit_log
from caesar.proactive.triggers import (
    HASource,
    ScheduleSource,
    Trigger,
    WebhookSource,
)
from caesar.proactive.webhook_dispatcher import (
    MAX_BODY_BYTES,
    WebhookDispatcher,
    _format_body,
    _trigger_with_body,
)


class _RecordingRunner:
    """ProactiveRunner stand-in: records every fire(trigger)."""

    def __init__(self) -> None:
        self.fired: list[Trigger] = []
        self.fail: Exception | None = None

    async def fire(self, trigger: Trigger) -> None:
        self.fired.append(trigger)
        if self.fail is not None:
            raise self.fail


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
    trigger_id: str = "github_pr_opened",
    enabled: bool = True,
    cooldown: int | None = None,
    bearer: str = "w" * 48,
    prompt: str = "brief me",
) -> Trigger:
    return Trigger(
        id=trigger_id,
        enabled=enabled,
        prompt=prompt,
        cooldown_seconds=cooldown,
        source=WebhookSource(bearer_token=SecretStr(bearer)),
    )


def _build_dispatcher(
    triggers: list[Trigger],
    audit_logger: AuditLogger,
    *,
    clock_value: list[datetime] | None = None,
) -> tuple[WebhookDispatcher, _RecordingRunner]:
    runner = _RecordingRunner()

    def clock() -> datetime:
        return (
            clock_value[0] if clock_value is not None else datetime(2026, 5, 18, 0, 0, tzinfo=UTC)
        )

    dispatcher = WebhookDispatcher(
        triggers,
        runner=runner,  # type: ignore[arg-type]
        audit=audit_logger,
        clock=clock,
    )
    return dispatcher, runner


# --- registration --------------------------------------------------------


async def test_only_enabled_webhook_triggers_registered(audit: AuditLogger) -> None:
    triggers = [
        _trigger(trigger_id="armed", enabled=True),
        _trigger(trigger_id="disarmed", enabled=False),
    ]
    dispatcher, _runner = _build_dispatcher(triggers, audit)
    assert dispatcher.armed_count == 1
    assert dispatcher.get("armed") is not None
    assert dispatcher.get("disarmed") is None


async def test_schedule_and_ha_triggers_are_ignored(audit: AuditLogger) -> None:
    """Other source kinds belong to Scheduler / HAEventDriver."""

    cron = Trigger(
        id="cron_one",
        prompt="x",
        source=ScheduleSource(cron="0 7 * * *"),
    )
    ha = Trigger(
        id="ha_one",
        prompt="x",
        source=HASource(event_type="state_changed", entity_id="binary_sensor.a", to="on"),
    )
    dispatcher, _runner = _build_dispatcher([cron, ha, _trigger(trigger_id="armed")], audit)
    assert dispatcher.armed_count == 1


# --- bearer verification ------------------------------------------------


async def test_verify_bearer_matches(audit: AuditLogger) -> None:
    trigger = _trigger(bearer="s" * 48)
    dispatcher, _runner = _build_dispatcher([trigger], audit)
    assert dispatcher.verify_bearer(trigger, "s" * 48) is True


async def test_verify_bearer_rejects_wrong(audit: AuditLogger) -> None:
    trigger = _trigger(bearer="s" * 48)
    dispatcher, _runner = _build_dispatcher([trigger], audit)
    assert dispatcher.verify_bearer(trigger, "WRONG") is False


async def test_verify_bearer_rejects_none(audit: AuditLogger) -> None:
    trigger = _trigger(bearer="s" * 48)
    dispatcher, _runner = _build_dispatcher([trigger], audit)
    assert dispatcher.verify_bearer(trigger, None) is False


# --- announce ------------------------------------------------------------


async def test_announce_emits_trigger_subscribed(audit: AuditLogger, engine: AsyncEngine) -> None:
    dispatcher, _runner = _build_dispatcher([_trigger(cooldown=30)], audit)
    await dispatcher.announce()
    subscribed = await _events_of_type(engine, "trigger.subscribed")
    assert len(subscribed) == 1
    payload = subscribed[0]["payload"]
    assert payload["trigger_id"] == "github_pr_opened"
    assert payload["source_kind"] == "webhook"
    assert payload["cooldown_seconds"] == 30


# --- audit helpers ------------------------------------------------------


async def test_record_received_writes_audit(audit: AuditLogger, engine: AsyncEngine) -> None:
    trigger = _trigger()
    dispatcher, _runner = _build_dispatcher([trigger], audit)
    await dispatcher.record_received(trigger, body_bytes=512, source_ip="127.0.0.1")
    rows = await _events_of_type(engine, "webhook.received")
    assert len(rows) == 1
    assert rows[0]["payload"]["trigger_id"] == "github_pr_opened"
    assert rows[0]["payload"]["body_bytes"] == 512
    assert rows[0]["payload"]["source_ip"] == "127.0.0.1"


async def test_record_unauthorized_omits_supplied_token(
    audit: AuditLogger, engine: AsyncEngine
) -> None:
    """The audit row must NEVER carry the supplied bearer."""

    dispatcher, _runner = _build_dispatcher([_trigger()], audit)
    await dispatcher.record_unauthorized("github_pr_opened", source_ip="1.2.3.4")
    rows = await _events_of_type(engine, "webhook.unauthorized")
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["trigger_id"] == "github_pr_opened"
    assert payload["source_ip"] == "1.2.3.4"
    # No bearer-shaped value anywhere in the payload.
    assert "Bearer" not in str(payload)
    assert "wht_" not in str(payload)
    assert "token" not in payload  # no token key


async def test_record_unknown_trigger_writes_audit(audit: AuditLogger, engine: AsyncEngine) -> None:
    dispatcher, _runner = _build_dispatcher([_trigger()], audit)
    await dispatcher.record_unknown_trigger("nonexistent", source_ip="1.2.3.4")
    rows = await _events_of_type(engine, "webhook.unknown_trigger")
    assert len(rows) == 1
    assert rows[0]["payload"]["trigger_id"] == "nonexistent"


# --- fire + cooldown ----------------------------------------------------


async def test_fire_invokes_runner_with_body_in_prompt(
    audit: AuditLogger, engine: AsyncEngine
) -> None:
    dispatcher, runner = _build_dispatcher([_trigger()], audit)
    body = b'{"action": "opened", "pr": 42}'
    await dispatcher.fire(dispatcher.get("github_pr_opened"), body)  # type: ignore[arg-type]
    assert len(runner.fired) == 1
    augmented = runner.fired[0]
    assert "Event body:" in augmented.prompt
    assert '"action": "opened"' in augmented.prompt
    assert '"pr": 42' in augmented.prompt
    # Source still preserved on the copy.
    assert isinstance(augmented.source, WebhookSource)


async def test_fire_brain_exception_is_audited_not_raised(
    audit: AuditLogger, engine: AsyncEngine
) -> None:
    """A brain failure inside fire() lands in audit; sender never sees it."""

    dispatcher, runner = _build_dispatcher([_trigger()], audit)
    runner.fail = RuntimeError("LLM exploded")
    await dispatcher.fire(dispatcher.get("github_pr_opened"), b"{}")  # type: ignore[arg-type]
    errors = await _events_of_type(engine, "trigger.error")
    assert len(errors) == 1
    assert errors[0]["payload"]["error"] == "RuntimeError"
    assert errors[0]["payload"]["message"] == "LLM exploded"


async def test_cooldown_blocks_repeat_fires(audit: AuditLogger, engine: AsyncEngine) -> None:
    clock_value = [datetime(2026, 5, 18, 0, 0, tzinfo=UTC)]
    dispatcher, runner = _build_dispatcher(
        [_trigger(cooldown=600)],
        audit,
        clock_value=clock_value,
    )
    trigger = dispatcher.get("github_pr_opened")
    assert trigger is not None
    await dispatcher.fire(trigger, b"{}")
    # Within cooldown — record_suppression is what the route would call.
    clock_value[0] = datetime(2026, 5, 18, 0, 0, 30, tzinfo=UTC)
    assert dispatcher.is_in_cooldown(trigger) is True
    dispatcher.record_suppression(trigger.id)
    clock_value[0] = datetime(2026, 5, 18, 0, 0, 45, tzinfo=UTC)
    assert dispatcher.is_in_cooldown(trigger) is True
    dispatcher.record_suppression(trigger.id)

    await dispatcher.stop()  # flushes suppressions
    suppressed = await _events_of_type(engine, "trigger.suppressed")
    assert len(suppressed) == 1
    assert suppressed[0]["payload"]["count"] == 2
    assert len(runner.fired) == 1


async def test_cooldown_elapses_allows_refire(audit: AuditLogger) -> None:
    clock_value = [datetime(2026, 5, 18, 0, 0, tzinfo=UTC)]
    dispatcher, runner = _build_dispatcher(
        [_trigger(cooldown=60)],
        audit,
        clock_value=clock_value,
    )
    trigger = dispatcher.get("github_pr_opened")
    assert trigger is not None
    await dispatcher.fire(trigger, b"{}")
    clock_value[0] = datetime(2026, 5, 18, 0, 0, 30, tzinfo=UTC)
    assert dispatcher.is_in_cooldown(trigger) is True
    clock_value[0] = datetime(2026, 5, 18, 0, 1, 30, tzinfo=UTC)
    assert dispatcher.is_in_cooldown(trigger) is False
    await dispatcher.fire(trigger, b"{}")
    assert len(runner.fired) == 2


async def test_no_cooldown_means_never_in_cooldown(audit: AuditLogger) -> None:
    dispatcher, _runner = _build_dispatcher([_trigger(cooldown=None)], audit)
    trigger = dispatcher.get("github_pr_opened")
    assert trigger is not None
    await dispatcher.fire(trigger, b"{}")
    assert dispatcher.is_in_cooldown(trigger) is False


async def test_cooldown_with_no_prior_fire_is_not_in_cooldown(
    audit: AuditLogger,
) -> None:
    dispatcher, _runner = _build_dispatcher([_trigger(cooldown=60)], audit)
    trigger = dispatcher.get("github_pr_opened")
    assert trigger is not None
    assert dispatcher.is_in_cooldown(trigger) is False


# --- spawn_fire (background task) --------------------------------------


async def test_spawn_fire_runs_in_background_and_stop_awaits(
    audit: AuditLogger,
) -> None:
    """spawn_fire returns immediately; stop() drains pending tasks."""

    dispatcher, runner = _build_dispatcher([_trigger()], audit)
    trigger = dispatcher.get("github_pr_opened")
    assert trigger is not None
    dispatcher.spawn_fire(trigger, b'{"x":1}')
    dispatcher.spawn_fire(trigger, b'{"x":2}')
    await dispatcher.stop()
    # Two fires landed (no cooldown, so both ran).
    assert len(runner.fired) == 2


# --- body formatting ---------------------------------------------------


def test_format_body_pretty_prints_json() -> None:
    out = _format_body(b'{"b":2,"a":1}')
    assert out == '{\n  "a": 1,\n  "b": 2\n}'


def test_format_body_passes_through_non_json_text() -> None:
    out = _format_body(b"not json, just text")
    assert out == "not json, just text"


def test_format_body_handles_empty() -> None:
    assert _format_body(b"") == "<empty>"


def test_format_body_handles_non_utf8() -> None:
    # 0xFF is invalid as a UTF-8 start byte; should still produce a
    # readable string instead of crashing.
    out = _format_body(b"\xff\xff")
    assert out  # non-empty; exact replacement chars not asserted


def test_trigger_with_body_preserves_metadata() -> None:
    trigger = _trigger(prompt="original instruction")
    augmented = _trigger_with_body(trigger, b"hello")
    assert augmented.id == trigger.id
    assert augmented.cooldown_seconds == trigger.cooldown_seconds
    assert "original instruction" in augmented.prompt
    assert "Event body:" in augmented.prompt
    assert "hello" in augmented.prompt


def test_max_body_bytes_is_64_kib() -> None:
    """Constant the FastAPI route reads — keep this assertion tight so
    a typo doesn't silently grow the limit."""

    assert MAX_BODY_BYTES == 64 * 1024
