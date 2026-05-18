"""Tests for the ProactiveRunner brain-entry helper (ADR-0030)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.db.settings_store import SettingsStore
from caesar.llm.gateway import ChatResponse, ToolUse
from caesar.policy.allowlist import AllowlistPolicy
from caesar.policy.yaml_loader import AllowedToolRule, RulesConfig
from caesar.proactive.runner import ProactiveRunner
from caesar.proactive.triggers import ScheduleSource, Trigger
from tests.conftest import FakeGateway


class _FakeRegistry:
    """Minimal WorkerRegistry stand-in (matches the protocol the brain uses)."""

    def __init__(
        self,
        capabilities: list[str],
        *,
        dispatch_result: dict[str, Any] | None = None,
    ) -> None:
        self._capabilities = capabilities
        self._result = dispatch_result or {}
        self.dispatch_calls: list[tuple[str, dict[str, Any]]] = []

    def find(self, capability: str) -> list[str]:
        return ["w1"] if capability in self._capabilities else []

    async def dispatch(
        self,
        capability: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
        decision_id: str | None = None,
    ) -> Any:
        from caesar.legion.protocol import TaskResult

        self.dispatch_calls.append((capability, payload))
        return TaskResult(
            task_id="fake",
            worker_id="w1",
            success=True,
            result=self._result,
        )


def _allow_notify_policy() -> AllowlistPolicy:
    return AllowlistPolicy(
        RulesConfig(
            version=1,
            allowed_services=[],
            allowed_tools=[AllowedToolRule(tool="notify")],
        )
    )


def _trigger() -> Trigger:
    return Trigger(
        id="morning_brief",
        prompt="brief me",
        source=ScheduleSource(cron="0 7 * * *"),
    )


async def test_runner_invokes_brain_with_proactive_system_prompt(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    """The runner threads the proactive-bias preamble into the system prompt
    so the LLM treats the run as a scheduled fire, not a chat turn."""

    audit = AuditLogger(engine)
    settings_store = SettingsStore(engine)
    registry = _FakeRegistry(capabilities=["tool.notify"])
    runner = ProactiveRunner(
        gateway=fake_gateway,
        ha=None,
        policy=_allow_notify_policy(),
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
        settings_store=settings_store,
        default_model="fake-model",
        default_prompt="You are CAESAR.",
    )

    await runner.fire(_trigger())

    assert fake_gateway.calls, "brain graph was never invoked"
    system_prompt = fake_gateway.calls[0]["system"]
    assert "running on a schedule" in system_prompt
    assert "summarise-and-notify" in system_prompt
    # Operator prompt rides last (so it can refine but not undo).
    assert system_prompt.endswith("You are CAESAR.")


async def test_runner_uses_operator_override_when_set(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    """When the dashboard has stored an override, the runner picks that up
    instead of the .env default."""

    audit = AuditLogger(engine)
    settings_store = SettingsStore(engine)
    await settings_store.set_system_prompt("Dashboard override.")
    runner = ProactiveRunner(
        gateway=fake_gateway,
        ha=None,
        policy=_allow_notify_policy(),
        audit=audit,
        registry=None,
        settings_store=settings_store,
        default_model="fake-model",
        default_prompt="You are CAESAR.",
    )

    await runner.fire(_trigger())
    system_prompt = fake_gateway.calls[0]["system"]
    assert system_prompt.endswith("Dashboard override.")


async def test_runner_dispatches_notify_tool_through_brain(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    """End-to-end: trigger.prompt → brain → notify tool dispatched.

    Fake gateway emits a notify tool_use; the registry receives the call;
    the audit log captures the notify.called row.
    """

    audit = AuditLogger(engine)
    settings_store = SettingsStore(engine)
    registry = _FakeRegistry(
        capabilities=["tool.notify"],
        dispatch_result={"id": "ntfy-42", "delivered_at": "2026-05-17T07:00:01+00:00"},
    )
    fake_gateway.queue(
        ChatResponse(
            content="",
            model="fake-model",
            input_tokens=1,
            output_tokens=2,
            stop_reason="tool_use",
            tool_uses=[
                ToolUse(
                    id="tu_notify",
                    name="notify",
                    input={"title": "Morning brief", "message": "All clear."},
                ),
            ],
        )
    )
    fake_gateway.queue(
        ChatResponse(content="Notified.", model="fake-model", input_tokens=3, output_tokens=4)
    )
    runner = ProactiveRunner(
        gateway=fake_gateway,
        ha=None,
        policy=_allow_notify_policy(),
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
        settings_store=settings_store,
        default_model="fake-model",
        default_prompt="You are CAESAR.",
    )

    await runner.fire(_trigger())

    assert registry.dispatch_calls == [
        ("tool.notify", {"title": "Morning brief", "message": "All clear."}),
    ]


async def test_runner_decision_id_is_proactive_prefixed(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    """Audit rows from a proactive run carry a decision_id that says so."""

    from sqlalchemy import select

    from caesar.db.schema import audit_log

    audit = AuditLogger(engine)
    settings_store = SettingsStore(engine)
    registry = _FakeRegistry(
        capabilities=["tool.notify"],
        dispatch_result={"id": "x", "delivered_at": "2026-05-17T07:00:01+00:00"},
    )
    fake_gateway.queue(
        ChatResponse(
            content="",
            model="fake-model",
            input_tokens=1,
            output_tokens=2,
            stop_reason="tool_use",
            tool_uses=[
                ToolUse(
                    id="tu_n",
                    name="notify",
                    input={"title": "t", "message": "m"},
                ),
            ],
        )
    )
    fake_gateway.queue(
        ChatResponse(content="done", model="fake-model", input_tokens=1, output_tokens=1)
    )
    runner = ProactiveRunner(
        gateway=fake_gateway,
        ha=None,
        policy=_allow_notify_policy(),
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
        settings_store=settings_store,
        default_model="fake-model",
        default_prompt="You are CAESAR.",
    )

    await runner.fire(_trigger())

    async with engine.begin() as conn:
        rows = list(await conn.execute(select(audit_log.c.event_type, audit_log.c.payload)))
    decision_ids = {r.payload.get("decision_id") for r in rows if "decision_id" in r.payload}
    assert decision_ids
    for did in decision_ids:
        assert did.startswith("proactive-morning_brief-")
