"""Tests for the dashboard cookie signing-key derivation (SR-006)."""

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
from caesar.praetor.dashboard.auth import (
    derive_signing_key,
    make_session_cookie,
    verify_session_cookie,
)

DASHBOARD_TOKEN = "the-token"
SEPARATE_SIGNING_KEY = "an-independent-32-byte-secret-or-longer"


# --- unit tests for derive_signing_key ---------------------------------------


def test_derive_uses_explicit_signing_key_when_set() -> None:
    settings = DashboardSettings(
        token=SecretStr("any-token"),
        signing_key=SecretStr(SEPARATE_SIGNING_KEY),
    )
    assert derive_signing_key(settings) == SEPARATE_SIGNING_KEY


def test_derive_from_token_when_signing_key_unset() -> None:
    settings = DashboardSettings(token=SecretStr(DASHBOARD_TOKEN))
    derived = derive_signing_key(settings)
    # Output is a 64-char hex digest of HMAC-SHA256.
    assert len(derived) == 64
    assert all(c in "0123456789abcdef" for c in derived)


def test_derived_key_depends_on_token() -> None:
    a = derive_signing_key(DashboardSettings(token=SecretStr("token-A")))
    b = derive_signing_key(DashboardSettings(token=SecretStr("token-B")))
    assert a != b


def test_derived_key_is_not_the_token() -> None:
    """The whole point of SR-006: the signing key is one step removed."""

    settings = DashboardSettings(token=SecretStr(DASHBOARD_TOKEN))
    assert derive_signing_key(settings) != DASHBOARD_TOKEN


def test_derive_raises_when_neither_token_nor_signing_key_set() -> None:
    settings = DashboardSettings(token=None, signing_key=None)
    with pytest.raises(ValueError, match="token is unset"):
        derive_signing_key(settings)


def test_rotating_token_invalidates_existing_cookie_in_default_mode() -> None:
    """With no signing_key set, rotating the token = log out everyone."""

    old = DashboardSettings(token=SecretStr("old-token"))
    cookie = make_session_cookie(derive_signing_key(old))

    new = DashboardSettings(token=SecretStr("new-token"))
    assert not verify_session_cookie(derive_signing_key(new), cookie)


def test_rotating_token_keeps_sessions_when_signing_key_is_separate() -> None:
    """With signing_key set, the cookie HMAC is independent of token.

    Operators who want to rotate the bearer secret without kicking
    everyone out can do so by leaving signing_key fixed.
    """

    key = SecretStr(SEPARATE_SIGNING_KEY)
    old = DashboardSettings(token=SecretStr("old-token"), signing_key=key)
    cookie = make_session_cookie(derive_signing_key(old))

    new = DashboardSettings(token=SecretStr("new-token"), signing_key=key)
    assert verify_session_cookie(derive_signing_key(new), cookie)


def test_rotating_signing_key_invalidates_existing_cookie() -> None:
    """Operators who want to revoke all sessions rotate signing_key."""

    old = DashboardSettings(
        token=SecretStr(DASHBOARD_TOKEN),
        signing_key=SecretStr("key-one"),
    )
    cookie = make_session_cookie(derive_signing_key(old))

    new = DashboardSettings(
        token=SecretStr(DASHBOARD_TOKEN),
        signing_key=SecretStr("key-two"),
    )
    assert not verify_session_cookie(derive_signing_key(new), cookie)


# --- end-to-end: the dashboard still authenticates correctly -----------------


def _settings_with_separate_key(db_url: str) -> CaesarSettings:
    return CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        dashboard=DashboardSettings(
            token=SecretStr(DASHBOARD_TOKEN),
            signing_key=SecretStr(SEPARATE_SIGNING_KEY),
        ),
    )


@pytest.fixture
async def app_with_separate_key(db_url: str, engine: AsyncEngine, fake_gateway):
    return create_app(
        settings=_settings_with_separate_key(db_url),
        gateway=fake_gateway,
        engine=engine,
    )


@pytest.fixture
async def client(app_with_separate_key) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_with_separate_key)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app_with_separate_key.router.lifespan_context(app_with_separate_key),
    ):
        yield ac


async def test_login_then_authed_request_round_trips_with_separate_signing_key(
    client: AsyncClient,
) -> None:
    """End-to-end: post the token, get a cookie, hit /dashboard authed."""

    r = await client.post(
        "/dashboard/login",
        data={"token": DASHBOARD_TOKEN},
        follow_redirects=False,
    )
    assert r.status_code == 303
    cookie = r.cookies.get("caesar_dashboard")
    assert cookie is not None

    # Cookie was signed with the separate signing key, not the auth token.
    assert verify_session_cookie(SEPARATE_SIGNING_KEY, cookie)
    # And it is NOT valid under the auth token alone (SR-006 win).
    assert not verify_session_cookie(DASHBOARD_TOKEN, cookie)

    # The cookie still authenticates against /dashboard.
    client.cookies.set("caesar_dashboard", cookie)
    r = await client.get("/dashboard")
    assert r.status_code == 200
