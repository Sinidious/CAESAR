"""Tests for the dashboard security headers (SR-010)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.config import (
    CaesarSettings,
    DashboardSettings,
    DatabaseSettings,
    LLMSettings,
    LogSettings,
)
from caesar.praetor.app import create_app
from caesar.praetor.dashboard.auth import derive_signing_key, make_session_cookie

DASHBOARD_TOKEN = "the-token"


def _settings(db_url: str) -> CaesarSettings:
    return CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        dashboard=DashboardSettings(token=SecretStr(DASHBOARD_TOKEN)),
    )


@pytest.fixture
async def app(db_url: str, engine: AsyncEngine, fake_gateway):
    return create_app(settings=_settings(db_url), gateway=fake_gateway, engine=engine)


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


async def test_dashboard_login_has_security_headers(client: AsyncClient) -> None:
    r = await client.get("/dashboard/login")
    assert r.status_code == 200
    csp = r.headers.get("Content-Security-Policy")
    assert csp is not None
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


async def test_dashboard_home_has_security_headers(client: AsyncClient) -> None:
    settings = DashboardSettings(token=SecretStr(DASHBOARD_TOKEN))
    client.cookies.set(
        "caesar_dashboard",
        make_session_cookie(derive_signing_key(settings)),
    )
    r = await client.get("/dashboard")
    assert r.status_code == 200
    assert "Content-Security-Policy" in r.headers


async def test_non_dashboard_route_does_not_get_csp(client: AsyncClient) -> None:
    """SR-010 is scoped to /dashboard/*; /v1/* and /metrics stay unchanged."""

    r = await client.get("/healthz")
    assert r.status_code == 200
    assert "Content-Security-Policy" not in r.headers


async def test_metrics_endpoint_does_not_get_csp(client: AsyncClient) -> None:
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "Content-Security-Policy" not in r.headers
