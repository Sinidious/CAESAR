"""Tests for the /v1/hook/{trigger_id} route (ADR-0032)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.config import CaesarSettings, DatabaseSettings, LLMSettings, LogSettings
from caesar.db.audit import AuditLogger
from caesar.praetor.app import create_app
from caesar.proactive.triggers import Trigger, WebhookSource
from caesar.proactive.webhook_dispatcher import MAX_BODY_BYTES, WebhookDispatcher


class _RecordingRunner:
    """ProactiveRunner stand-in: records every fire."""

    def __init__(self) -> None:
        self.fired: list[Trigger] = []

    async def fire(self, trigger: Trigger) -> None:
        self.fired.append(trigger)


def _settings(db_url: str) -> CaesarSettings:
    return CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
    )


def _trigger(*, trigger_id: str = "github_pr_opened", bearer: str = "w" * 48) -> Trigger:
    return Trigger(
        id=trigger_id,
        prompt="brief me",
        source=WebhookSource(bearer_token=SecretStr(bearer)),
    )


@pytest.fixture
async def webhook_client(
    db_url: str, engine: AsyncEngine, fake_gateway
) -> AsyncIterator[tuple[AsyncClient, _RecordingRunner, WebhookDispatcher]]:
    """A FastAPI client with the webhook route wired to a real dispatcher."""

    app = create_app(settings=_settings(db_url), gateway=fake_gateway, engine=engine)
    runner = _RecordingRunner()
    audit = AuditLogger(engine, max_string_chars=4096)
    dispatcher = WebhookDispatcher(
        [_trigger()],
        runner=runner,  # type: ignore[arg-type]
        audit=audit,
    )
    app.state.webhook_dispatcher = dispatcher
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac, runner, dispatcher
    await dispatcher.stop()


# --- happy path ---------------------------------------------------------


async def test_valid_bearer_accepts_and_fires(webhook_client) -> None:
    client, runner, _dispatcher = webhook_client
    resp = await client.post(
        "/v1/hook/github_pr_opened",
        headers={"Authorization": "Bearer " + "w" * 48},
        json={"action": "opened", "pr": 42},
    )
    assert resp.status_code == 202
    # Background fire must land. The dispatcher.stop() in the fixture
    # awaits it, but we want to assert here without forcing close.
    for _ in range(40):
        await asyncio.sleep(0.01)
        if runner.fired:
            break
    assert len(runner.fired) == 1
    assert "Event body:" in runner.fired[0].prompt
    assert '"pr": 42' in runner.fired[0].prompt


# --- auth failures ------------------------------------------------------


async def test_missing_authorization_returns_401(webhook_client) -> None:
    client, runner, _ = webhook_client
    resp = await client.post(
        "/v1/hook/github_pr_opened",
        json={"x": 1},
    )
    assert resp.status_code == 401
    await asyncio.sleep(0.05)
    assert runner.fired == []


async def test_wrong_bearer_returns_401(webhook_client) -> None:
    client, runner, _ = webhook_client
    resp = await client.post(
        "/v1/hook/github_pr_opened",
        headers={"Authorization": "Bearer NOT_THE_RIGHT_TOKEN_AT_ALL"},
        json={},
    )
    assert resp.status_code == 401
    await asyncio.sleep(0.05)
    assert runner.fired == []


async def test_malformed_authorization_returns_401(webhook_client) -> None:
    client, runner, _ = webhook_client
    # No "Bearer " prefix.
    resp = await client.post(
        "/v1/hook/github_pr_opened",
        headers={"Authorization": "w" * 48},
        json={},
    )
    assert resp.status_code == 401
    await asyncio.sleep(0.05)
    assert runner.fired == []


# --- unknown trigger ---------------------------------------------------


async def test_unknown_trigger_returns_404(webhook_client) -> None:
    client, runner, _ = webhook_client
    resp = await client.post(
        "/v1/hook/does_not_exist",
        headers={"Authorization": "Bearer " + "w" * 48},
        json={},
    )
    assert resp.status_code == 404
    await asyncio.sleep(0.05)
    assert runner.fired == []


async def test_route_404s_when_dispatcher_not_wired(
    db_url: str, engine: AsyncEngine, fake_gateway
) -> None:
    """No webhook triggers configured ⇒ dispatcher is None ⇒ 404 still."""

    app = create_app(settings=_settings(db_url), gateway=fake_gateway, engine=engine)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        resp = await ac.post(
            "/v1/hook/whatever",
            headers={"Authorization": "Bearer " + "w" * 48},
            json={},
        )
    assert resp.status_code == 404


# --- size limit ---------------------------------------------------------


async def test_oversize_body_returns_413(webhook_client) -> None:
    client, runner, _ = webhook_client
    big_body = b"x" * (MAX_BODY_BYTES + 1)
    resp = await client.post(
        "/v1/hook/github_pr_opened",
        headers={
            "Authorization": "Bearer " + "w" * 48,
            "Content-Type": "application/octet-stream",
        },
        content=big_body,
    )
    assert resp.status_code == 413
    await asyncio.sleep(0.05)
    assert runner.fired == []


# --- cooldown -----------------------------------------------------------


async def test_cooldown_returns_429_and_coalesces(
    db_url: str, engine: AsyncEngine, fake_gateway
) -> None:
    """Inside cooldown ⇒ 429; suppressed events coalesce."""

    app = create_app(settings=_settings(db_url), gateway=fake_gateway, engine=engine)
    runner = _RecordingRunner()
    audit = AuditLogger(engine, max_string_chars=4096)
    dispatcher = WebhookDispatcher(
        [
            Trigger(
                id="github_pr_opened",
                prompt="brief me",
                cooldown_seconds=600,
                source=WebhookSource(bearer_token=SecretStr("w" * 48)),
            )
        ],
        runner=runner,  # type: ignore[arg-type]
        audit=audit,
    )
    app.state.webhook_dispatcher = dispatcher
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        # First fire — 202.
        first = await ac.post(
            "/v1/hook/github_pr_opened",
            headers={"Authorization": "Bearer " + "w" * 48},
            json={},
        )
        assert first.status_code == 202
        # Wait for the background task so cooldown's last_fired is set.
        for _ in range(40):
            await asyncio.sleep(0.01)
            if runner.fired:
                break
        # Second within cooldown — 429.
        second = await ac.post(
            "/v1/hook/github_pr_opened",
            headers={"Authorization": "Bearer " + "w" * 48},
            json={},
        )
        assert second.status_code == 429
        # Third too.
        third = await ac.post(
            "/v1/hook/github_pr_opened",
            headers={"Authorization": "Bearer " + "w" * 48},
            json={},
        )
        assert third.status_code == 429

    # stop() flushed suppression.
    await dispatcher.stop()
    assert len(runner.fired) == 1
