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
from caesar.db.audit import AuditLogger
from caesar.praetor.app import create_app
from caesar.praetor.dashboard.auth import (
    make_session_cookie,
    token_matches,
    verify_session_cookie,
)

DASHBOARD_TOKEN = "shared-token-please-let-me-in"


def _settings(db_url: str, *, token: str | None = DASHBOARD_TOKEN) -> CaesarSettings:
    return CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        dashboard=DashboardSettings(
            token=SecretStr(token) if token else None,
            cookie_max_age_seconds=60,
        ),
    )


@pytest.fixture
async def dashboard_app(db_url: str, engine: AsyncEngine, fake_gateway):
    """Build the FastAPI app with dashboard mounted; expose for tests."""

    return create_app(settings=_settings(db_url), gateway=fake_gateway, engine=engine)


@pytest.fixture
async def dashboard_client(dashboard_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=dashboard_app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        dashboard_app.router.lifespan_context(dashboard_app),
    ):
        yield ac


# --- auth helpers ------------------------------------------------------------


def test_cookie_round_trip() -> None:
    cookie = make_session_cookie(DASHBOARD_TOKEN)
    assert verify_session_cookie(DASHBOARD_TOKEN, cookie)
    assert not verify_session_cookie("different-token", cookie)
    assert not verify_session_cookie(DASHBOARD_TOKEN, "garbage")


def test_token_matches() -> None:
    settings = DashboardSettings(token=SecretStr("abc"))
    assert token_matches("abc", settings)
    assert not token_matches("abd", settings)
    assert not token_matches("abc", DashboardSettings(token=None))


# --- routing -----------------------------------------------------------------


def test_dashboard_not_mounted_when_token_unset(db_url: str, engine, fake_gateway):
    """No token configured → /dashboard/* returns 404."""

    from fastapi.testclient import TestClient

    app = create_app(settings=_settings(db_url, token=None), gateway=fake_gateway, engine=engine)
    with TestClient(app) as tc:
        r = tc.get("/dashboard/login")
        assert r.status_code == 404


async def test_login_get_returns_form(dashboard_client: AsyncClient) -> None:
    r = await dashboard_client.get("/dashboard/login")
    assert r.status_code == 200
    assert 'name="token"' in r.text


async def test_login_post_with_bad_token_returns_401(
    dashboard_client: AsyncClient,
) -> None:
    r = await dashboard_client.post("/dashboard/login", data={"token": "wrong"})
    assert r.status_code == 401
    assert "Invalid token" in r.text


async def test_login_post_with_valid_token_sets_cookie_and_redirects(
    dashboard_client: AsyncClient,
) -> None:
    r = await dashboard_client.post(
        "/dashboard/login",
        data={"token": DASHBOARD_TOKEN},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"
    assert "caesar_dashboard" in r.cookies


async def test_home_requires_auth(dashboard_client: AsyncClient) -> None:
    r = await dashboard_client.get("/dashboard")
    assert r.status_code == 401


async def test_home_with_session_cookie_returns_html(
    dashboard_client: AsyncClient,
) -> None:
    dashboard_client.cookies.set("caesar_dashboard", make_session_cookie(DASHBOARD_TOKEN))
    r = await dashboard_client.get("/dashboard")
    assert r.status_code == 200
    assert "CAESAR" in r.text
    assert "/dashboard/audit" in r.text


async def test_audit_fragment_renders_rows(
    dashboard_client: AsyncClient, engine: AsyncEngine
) -> None:
    # Seed an audit row.
    audit = AuditLogger(engine)
    await audit.record("chat.completed", {"reply": "hello world"})

    dashboard_client.cookies.set("caesar_dashboard", make_session_cookie(DASHBOARD_TOKEN))
    r = await dashboard_client.get("/dashboard/audit")
    assert r.status_code == 200
    assert "chat.completed" in r.text


async def test_audit_fragment_empty_state(
    dashboard_client: AsyncClient,
) -> None:
    dashboard_client.cookies.set("caesar_dashboard", make_session_cookie(DASHBOARD_TOKEN))
    r = await dashboard_client.get("/dashboard/audit")
    assert r.status_code == 200
    assert "No audit events" in r.text


async def test_logout_clears_cookie(dashboard_client: AsyncClient) -> None:
    dashboard_client.cookies.set("caesar_dashboard", make_session_cookie(DASHBOARD_TOKEN))
    r = await dashboard_client.post("/dashboard/logout", follow_redirects=False)
    assert r.status_code == 303
    # Cookie cleared via Set-Cookie with Max-Age=0.
    set_cookie = r.headers.get("set-cookie", "")
    assert "caesar_dashboard" in set_cookie


# --- SSE ---------------------------------------------------------------------


async def test_audit_stream_requires_auth(dashboard_client: AsyncClient) -> None:
    r = await dashboard_client.get("/dashboard/audit/stream")
    assert r.status_code == 401


# End-to-end SSE streaming through httpx ASGITransport doesn't work — the
# transport buffers the entire response. The streaming is verified by
# AuditEventBus unit tests + the auth check above; full e2e with real
# uvicorn lives in a future manual / integration test.
