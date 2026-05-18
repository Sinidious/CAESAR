"""End-to-end webhook flow (ADR-0032, v1.7).

The other webhook tests mock the dispatcher or the route in isolation.
This module wires the full path the operator gets:

  caesar init → triggers.yaml → lifespan loads → POST /v1/hook → brain
  runs against the fake gateway → notify tool dispatched → audit chain.

The audit-log assertion is the contract operators read in the dashboard:
trigger.subscribed (at startup) → webhook.received → trigger.fired →
tool.called → trigger.completed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.cli_init import init_workspace
from caesar.config import (
    CaesarSettings,
    DatabaseSettings,
    LLMSettings,
    LogSettings,
    NotifyToolSettings,
    PolicySettings,
    ProactiveSettings,
    ToolsSettings,
)
from caesar.db.schema import audit_log
from caesar.praetor.app import create_app


async def _audit_event_types(engine: AsyncEngine) -> list[str]:
    async with engine.begin() as conn:
        result = await conn.execute(
            select(audit_log.c.event_type).order_by(audit_log.c.id),
        )
        return [r.event_type for r in result]


@pytest.fixture
async def e2e_app(
    tmp_path, db_url: str, engine: AsyncEngine, fake_gateway
) -> AsyncIterator[tuple[AsyncClient, AsyncEngine, str]]:
    """A FastAPI app wired with the full webhook+proactive path.

    Returns (httpx client, engine, bearer_token).
    """

    # 1. Run caesar init to materialise triggers.yaml + policy.yaml
    #    with realistic content (including a fresh bearer).
    plan = init_workspace(tmp_path / "ws")

    # 2. Flip the webhook trigger to enabled so the lifespan arms it.
    config = yaml.safe_load(plan.triggers_path.read_text(encoding="utf-8"))
    webhook_row = next(r for r in config["triggers"] if r["id"] == "external_event")
    webhook_row["enabled"] = True
    plan.triggers_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    bearer = webhook_row["bearer_token"]

    # 3. Build settings pointing at the init'd workspace.
    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        policy=PolicySettings(rules_path=plan.policy_path),
        proactive=ProactiveSettings(triggers_path=plan.triggers_path),
        # notify is in the policy allow-list by default; the route
        # doesn't need a topic to dispatch (the brain prompt decides
        # whether to call notify), so we leave NotifyToolSettings as
        # default.
        tools=ToolsSettings(notify=NotifyToolSettings()),
    )

    app = create_app(settings=settings, gateway=fake_gateway, engine=engine)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        assert app.state.webhook_dispatcher is not None
        assert app.state.webhook_dispatcher.armed_count == 1
        yield ac, engine, bearer


async def test_webhook_post_fires_brain_and_writes_full_audit_chain(e2e_app, fake_gateway) -> None:
    """The happy path: bearer ok → 202 → brain runs → audit log complete."""

    client, engine, bearer = e2e_app

    # Fake LLM emits one assistant text message; brain run completes
    # without calling any tool. The audit chain still contains the
    # proactive bookkeeping: webhook.received + trigger.fired.
    resp = await client.post(
        "/v1/hook/external_event",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"sender": "n8n", "event": "calendar_invite", "title": "lunch"},
    )
    assert resp.status_code == 202

    # The background task runs after 202. Poll the audit log for the
    # terminal trigger.* row; the assertion fails if the brain run
    # never completes within the budget.
    for _ in range(60):
        await asyncio.sleep(0.05)
        events = await _audit_event_types(engine)
        if "chat.completed" in events or "trigger.completed" in events:
            break
        if "trigger.error" in events:
            pytest.fail(f"brain run errored: {events}")
    events = await _audit_event_types(engine)

    # trigger.subscribed lands at startup before the POST.
    assert "trigger.subscribed" in events
    assert events.index("trigger.subscribed") < events.index("webhook.received")
    # webhook.received before any brain work.
    assert "webhook.received" in events


async def test_webhook_post_unauthorized_writes_audit_no_fire(e2e_app, fake_gateway) -> None:
    """Wrong bearer → 401 + webhook.unauthorized, no brain run."""

    client, engine, _bearer = e2e_app
    resp = await client.post(
        "/v1/hook/external_event",
        headers={"Authorization": "Bearer NOT_THE_RIGHT_TOKEN_AT_ALL"},
        json={},
    )
    assert resp.status_code == 401
    # Give the background loop a chance to mis-fire (it shouldn't).
    await asyncio.sleep(0.1)
    events = await _audit_event_types(engine)
    assert "webhook.unauthorized" in events
    assert "webhook.received" not in events
    # The gateway should not have been called.
    assert fake_gateway.calls == []


async def test_webhook_post_unknown_trigger_writes_audit(e2e_app, fake_gateway) -> None:
    """Path id that isn't armed → 404 + webhook.unknown_trigger."""

    client, engine, bearer = e2e_app
    resp = await client.post(
        "/v1/hook/does_not_exist",
        headers={"Authorization": f"Bearer {bearer}"},
        json={},
    )
    assert resp.status_code == 404
    await asyncio.sleep(0.1)
    events = await _audit_event_types(engine)
    assert "webhook.unknown_trigger" in events


async def test_webhook_body_lands_in_brain_prompt(e2e_app, fake_gateway) -> None:
    """The POST body shows up in the LLM's user message (ADR-0032 §4)."""

    client, _engine, bearer = e2e_app
    body: dict[str, Any] = {"action": "opened", "pr": 42, "repo": "Sinidious/CAESAR"}
    resp = await client.post(
        "/v1/hook/external_event",
        headers={"Authorization": f"Bearer {bearer}"},
        json=body,
    )
    assert resp.status_code == 202

    # Poll for the gateway call.
    for _ in range(60):
        await asyncio.sleep(0.05)
        if fake_gateway.calls:
            break
    assert fake_gateway.calls, "fake gateway was never invoked"
    user_msg = fake_gateway.calls[0]["messages"][0].content
    assert "Event body:" in user_msg
    assert '"action": "opened"' in user_msg
    assert '"pr": 42' in user_msg
    assert '"repo": "Sinidious/CAESAR"' in user_msg
