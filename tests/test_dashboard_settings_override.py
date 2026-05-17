"""Tests for the system-prompt override visibility (SR-012).

Two things must happen when an operator overrides the LLM system
prompt from the dashboard:

1. A warning shows up in the structured logs so an operator scanning
   ``journalctl`` sees the change, not just the audit row. The
   structured log is *fire-and-forget* at the source — covered by
   the inline ``logger.warning`` call in
   ``caesar.praetor.dashboard.routes``; we don't pytest-assert on
   structlog plumbing.
2. The settings page renders a prominent banner so the next operator
   who opens the page sees that the prompt isn't the env default.
   That's what these tests check.
"""

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
        client = ac
        sample_settings = DashboardSettings(token=SecretStr(DASHBOARD_TOKEN))
        client.cookies.set(
            "caesar_dashboard",
            make_session_cookie(derive_signing_key(sample_settings)),
        )
        yield client


async def test_settings_page_renders_warning_banner_when_overridden(
    client: AsyncClient,
) -> None:
    # First, set an override.
    r = await client.post(
        "/dashboard/settings",
        data={"system_prompt": "Override!"},
    )
    assert r.status_code == 200
    # Then load the settings page fresh and confirm the banner renders.
    r = await client.get("/dashboard/settings")
    assert r.status_code == 200
    assert "banner-warning" in r.text
    assert "System prompt override is active" in r.text


async def test_settings_page_omits_banner_with_no_override(client: AsyncClient) -> None:
    r = await client.get("/dashboard/settings")
    assert r.status_code == 200
    assert "banner-warning" not in r.text
    assert "System prompt override is active" not in r.text
