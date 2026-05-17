"""Tests for the login rate-limiter (SR-002)."""

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
from caesar.praetor.dashboard.rate_limit import LoginRateLimiter

DASHBOARD_TOKEN = "shared-token-please-let-me-in"


# --- unit tests for the limiter itself ---------------------------------------


def test_limiter_allows_attempts_under_the_limit() -> None:
    limiter = LoginRateLimiter(max_failures=3, window_seconds=60.0)
    assert limiter.check("1.2.3.4", now=0.0)
    limiter.record_failure("1.2.3.4", now=0.0)
    assert limiter.check("1.2.3.4", now=1.0)
    limiter.record_failure("1.2.3.4", now=1.0)
    assert limiter.check("1.2.3.4", now=2.0)
    limiter.record_failure("1.2.3.4", now=2.0)
    # 3rd failure recorded; bucket is full.
    assert not limiter.check("1.2.3.4", now=3.0)


def test_limiter_isolates_keys() -> None:
    limiter = LoginRateLimiter(max_failures=1, window_seconds=60.0)
    limiter.record_failure("1.2.3.4", now=0.0)
    assert not limiter.check("1.2.3.4", now=0.0)
    assert limiter.check("5.6.7.8", now=0.0)


def test_limiter_recovers_after_window_passes() -> None:
    limiter = LoginRateLimiter(max_failures=2, window_seconds=60.0)
    limiter.record_failure("ip", now=0.0)
    limiter.record_failure("ip", now=10.0)
    assert not limiter.check("ip", now=20.0)
    # First failure ages out at t=60.0 (window_seconds after t=0.0).
    assert limiter.check("ip", now=61.0)


def test_retry_after_seconds_reports_remaining_window() -> None:
    limiter = LoginRateLimiter(max_failures=1, window_seconds=60.0)
    assert limiter.retry_after_seconds("ip", now=0.0) == 0.0
    limiter.record_failure("ip", now=0.0)
    assert limiter.retry_after_seconds("ip", now=0.0) == pytest.approx(60.0)
    assert limiter.retry_after_seconds("ip", now=30.0) == pytest.approx(30.0)
    # After the window expires, the slot is free again.
    assert limiter.retry_after_seconds("ip", now=60.1) == 0.0


# --- end-to-end test through the dashboard route -----------------------------


def _settings(db_url: str) -> CaesarSettings:
    return CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        dashboard=DashboardSettings(
            token=SecretStr(DASHBOARD_TOKEN),
            cookie_max_age_seconds=60,
        ),
    )


@pytest.fixture
async def dashboard_app(db_url: str, engine: AsyncEngine, fake_gateway):
    return create_app(settings=_settings(db_url), gateway=fake_gateway, engine=engine)


@pytest.fixture
async def dashboard_client(dashboard_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=dashboard_app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        dashboard_app.router.lifespan_context(dashboard_app),
    ):
        yield ac


async def test_dashboard_mounts_limiter_on_app_state(dashboard_app) -> None:
    assert isinstance(dashboard_app.state.login_rate_limiter, LoginRateLimiter)


async def test_login_returns_429_after_max_failures(
    dashboard_client: AsyncClient, dashboard_app
) -> None:
    """5 bad attempts get 401; the 6th gets 429 with a Retry-After header."""

    limiter: LoginRateLimiter = dashboard_app.state.login_rate_limiter
    for _ in range(limiter.max_failures):
        r = await dashboard_client.post("/dashboard/login", data={"token": "wrong"})
        assert r.status_code == 401
    r = await dashboard_client.post("/dashboard/login", data={"token": "wrong"})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    assert "Too many failed attempts" in r.text


async def test_successful_login_does_not_consume_bucket(
    dashboard_client: AsyncClient, dashboard_app
) -> None:
    """A correct login doesn't count as a failure."""

    limiter: LoginRateLimiter = dashboard_app.state.login_rate_limiter
    for _ in range(limiter.max_failures + 5):
        r = await dashboard_client.post(
            "/dashboard/login",
            data={"token": DASHBOARD_TOKEN},
            follow_redirects=False,
        )
        assert r.status_code == 303
    # Bucket still empty; another bad attempt should still get 401, not 429.
    r = await dashboard_client.post("/dashboard/login", data={"token": "wrong"})
    assert r.status_code == 401


async def test_limiter_unblocks_after_window(dashboard_client: AsyncClient, dashboard_app) -> None:
    """Aging out via the limiter's internal clock unblocks the route."""

    limiter: LoginRateLimiter = dashboard_app.state.login_rate_limiter
    # Hand-set the failures so we don't have to wait 5 real minutes.
    limiter._failures["testclient"].extend([-limiter.window_seconds - 1.0] * limiter.max_failures)
    r = await dashboard_client.post("/dashboard/login", data={"token": "wrong"})
    assert r.status_code == 401
