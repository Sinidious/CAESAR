"""Tests for optional bearer auth on /metrics (SR-003)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.config import (
    CaesarSettings,
    DatabaseSettings,
    LLMSettings,
    LogSettings,
    MetricsSettings,
)
from caesar.praetor.app import create_app

METRICS_TOKEN = "scrape-me-securely"


def _settings(db_url: str, *, token: str | None) -> CaesarSettings:
    return CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        metrics=MetricsSettings(token=SecretStr(token) if token else None),
    )


@pytest.fixture
async def open_client(db_url: str, engine: AsyncEngine, fake_gateway) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings=_settings(db_url, token=None),
        gateway=fake_gateway,
        engine=engine,
    )
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


@pytest.fixture
async def auth_client(db_url: str, engine: AsyncEngine, fake_gateway) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings=_settings(db_url, token=METRICS_TOKEN),
        gateway=fake_gateway,
        engine=engine,
    )
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


async def test_metrics_open_when_no_token_configured(open_client: AsyncClient) -> None:
    r = await open_client.get("/metrics")
    assert r.status_code == 200
    assert "caesar_audit_events_total" in r.text


async def test_metrics_rejects_unauthenticated_when_token_configured(
    auth_client: AsyncClient,
) -> None:
    r = await auth_client.get("/metrics")
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"].startswith("Bearer")


async def test_metrics_rejects_wrong_token(auth_client: AsyncClient) -> None:
    r = await auth_client.get(
        "/metrics",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401


async def test_metrics_rejects_non_bearer_scheme(auth_client: AsyncClient) -> None:
    r = await auth_client.get(
        "/metrics",
        headers={"Authorization": f"Basic {METRICS_TOKEN}"},
    )
    assert r.status_code == 401


async def test_metrics_accepts_valid_bearer_token(auth_client: AsyncClient) -> None:
    r = await auth_client.get(
        "/metrics",
        headers={"Authorization": f"Bearer {METRICS_TOKEN}"},
    )
    assert r.status_code == 200
    assert "caesar_audit_events_total" in r.text


async def test_metrics_bearer_scheme_is_case_insensitive(auth_client: AsyncClient) -> None:
    """RFC 6750: the scheme is case-insensitive."""

    r = await auth_client.get(
        "/metrics",
        headers={"Authorization": f"bearer {METRICS_TOKEN}"},
    )
    assert r.status_code == 200
