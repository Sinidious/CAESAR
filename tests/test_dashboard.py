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
    derive_signing_key,
    make_session_cookie,
    token_matches,
    verify_session_cookie,
)

DASHBOARD_TOKEN = "shared-token-please-let-me-in"


def _signed_cookie(token: str = DASHBOARD_TOKEN) -> str:
    """Build a valid session cookie using the configured derivation.

    Mirrors what ``post_login`` does so tests don't have to know
    whether the signing key is derived or operator-supplied.
    """

    settings = DashboardSettings(token=SecretStr(token))
    return make_session_cookie(derive_signing_key(settings))


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
    """``make_session_cookie`` / ``verify_session_cookie`` accept a raw
    signing key and operate symmetrically. The signing key here is
    a constant; integration tests cover the derived-key path."""

    cookie = make_session_cookie("signing-key-1")
    assert verify_session_cookie("signing-key-1", cookie)
    assert not verify_session_cookie("signing-key-2", cookie)
    assert not verify_session_cookie("signing-key-1", "garbage")


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
    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
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

    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
    r = await dashboard_client.get("/dashboard/audit")
    assert r.status_code == 200
    assert "chat.completed" in r.text


async def test_audit_fragment_empty_state(
    dashboard_client: AsyncClient,
) -> None:
    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
    r = await dashboard_client.get("/dashboard/audit")
    assert r.status_code == 200
    assert "No audit events" in r.text


async def test_logout_clears_cookie(dashboard_client: AsyncClient) -> None:
    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
    r = await dashboard_client.post("/dashboard/logout", follow_redirects=False)
    assert r.status_code == 303
    # Cookie cleared via Set-Cookie with Max-Age=0.
    set_cookie = r.headers.get("set-cookie", "")
    assert "caesar_dashboard" in set_cookie


# --- SSE ---------------------------------------------------------------------


async def test_audit_stream_requires_auth(dashboard_client: AsyncClient) -> None:
    r = await dashboard_client.get("/dashboard/audit/stream")
    assert r.status_code == 401


async def test_intents_requires_auth(dashboard_client: AsyncClient) -> None:
    r = await dashboard_client.get("/dashboard/intents")
    assert r.status_code == 401


async def test_intents_empty_state(dashboard_client: AsyncClient) -> None:
    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
    r = await dashboard_client.get("/dashboard/intents")
    assert r.status_code == 200
    assert "No intents yet" in r.text


async def test_intents_renders_grouped_events(
    dashboard_client: AsyncClient, engine: AsyncEngine
) -> None:
    audit = AuditLogger(engine)
    await audit.record(
        "chat.completed",
        {
            "decision_id": "d-int",
            "messages": [{"role": "user", "content": "turn it on"}],
            "reply": "Done.",
        },
    )
    await audit.record(
        "service.called",
        {"decision_id": "d-int", "domain": "light", "service": "turn_on"},
    )
    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
    r = await dashboard_client.get("/dashboard/intents")
    assert r.status_code == 200
    assert "turn it on" in r.text
    assert "service.called" in r.text


async def test_agents_requires_auth(dashboard_client: AsyncClient) -> None:
    r = await dashboard_client.get("/dashboard/agents")
    assert r.status_code == 401


async def test_agents_empty_when_bus_disabled(
    dashboard_client: AsyncClient,
) -> None:
    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
    r = await dashboard_client.get("/dashboard/agents")
    assert r.status_code == 200
    assert "Bus disabled" in r.text


async def test_agents_shows_dispatch_history(
    dashboard_client: AsyncClient, engine: AsyncEngine
) -> None:
    audit = AuditLogger(engine)
    await audit.record(
        "legion.dispatched",
        {
            "decision_id": "d-A",
            "task_id": "abcdef1234567890",
            "capability": "memory.recall",
            "worker_id": "memory_recall",
            "success": True,
            "error": None,
        },
    )
    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
    r = await dashboard_client.get("/dashboard/agents")
    assert r.status_code == 200
    assert "memory.recall" in r.text
    assert "abcdef12" in r.text  # truncated task id


async def test_home_nav_links_present(dashboard_client: AsyncClient) -> None:
    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
    r = await dashboard_client.get("/dashboard")
    assert r.status_code == 200
    assert "/dashboard/intents" in r.text
    assert "/dashboard/agents" in r.text
    assert "/dashboard/settings" in r.text


# --- settings page ----------------------------------------------------------


async def test_settings_requires_auth(dashboard_client: AsyncClient) -> None:
    r = await dashboard_client.get("/dashboard/settings")
    assert r.status_code == 401


async def test_settings_get_shows_env_default(dashboard_client: AsyncClient) -> None:
    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
    r = await dashboard_client.get("/dashboard/settings")
    assert r.status_code == 200
    assert "Showing the env default" in r.text
    assert "You are CAESAR" in r.text  # default prompt


async def test_settings_post_saves_and_takes_effect(
    dashboard_client: AsyncClient, dashboard_app
) -> None:
    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
    r = await dashboard_client.post(
        "/dashboard/settings",
        data={"system_prompt": "You are CAESAR. Concise to a fault."},
    )
    assert r.status_code == 200
    assert "Saved" in r.text
    # Persisted in the store.
    stored = await dashboard_app.state.settings_store.get_system_prompt()
    assert stored == "You are CAESAR. Concise to a fault."


async def test_settings_post_writes_audit_row(dashboard_client: AsyncClient, engine) -> None:
    from sqlalchemy import select

    from caesar.db.schema import audit_log

    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
    await dashboard_client.post(
        "/dashboard/settings",
        data={"system_prompt": "New voice."},
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(select(audit_log))).all()
    assert any(r.event_type == "settings.updated" for r in rows)


async def test_settings_post_empty_does_not_save(
    dashboard_client: AsyncClient, dashboard_app
) -> None:
    """Empty (or whitespace-only) submissions don't overwrite."""

    dashboard_client.cookies.set("caesar_dashboard", _signed_cookie())
    await dashboard_client.post(
        "/dashboard/settings",
        data={"system_prompt": "   "},
    )
    stored = await dashboard_app.state.settings_store.get_system_prompt()
    assert stored is None


# End-to-end SSE streaming through httpx ASGITransport doesn't work — the
# transport buffers the entire response. The streaming is verified by
# AuditEventBus unit tests + the auth check above; full e2e with real
# uvicorn lives in a future manual / integration test.
