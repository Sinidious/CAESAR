"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.bus.client import Bus
from caesar.config import CaesarSettings, DatabaseSettings, LLMSettings, LogSettings
from caesar.db.engine import create_engine
from caesar.db.migrate import upgrade_to_head
from caesar.ha.client import HAClient
from caesar.ha.models import ServiceCall
from caesar.legion.registry import WorkerRegistry
from caesar.llm.gateway import ChatMessage, ChatResponse, LLMGateway, ToolDefinition
from caesar.policy.engine import Policy, PolicyDecision
from tests.fakebus import find_nats_binary, spawn_nats
from tests.fakeha import VALID_TOKEN, make_rest_app


class FakeGateway:
    """A deterministic LLMGateway for tests.

    Records every call and returns a canned reply. If ``scripted`` is
    non-empty, the next call pops a response from that queue instead
    of returning the default text — useful for driving the brain
    graph's tool-use loop deterministically.

    Implements the Protocol structurally so type checkers accept it
    wherever an ``LLMGateway`` is required.
    """

    def __init__(self, reply: str = "hello back") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []
        self.scripted: list[ChatResponse] = []

    def queue(self, response: ChatResponse) -> None:
        self.scripted.append(response)

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> ChatResponse:
        self.calls.append(
            {
                "messages": messages,
                "system": system,
                "model": model,
                "max_tokens": max_tokens,
                "tools": tools,
            }
        )
        if self.scripted:
            return self.scripted.pop(0)
        return ChatResponse(
            content=self.reply,
            model=model or "fake-model",
            input_tokens=1,
            output_tokens=2,
        )


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    """A unique async SQLite URL per test, on disk under tmp_path."""

    return f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite3'}"


@pytest.fixture
def settings(db_url: str) -> CaesarSettings:
    """Test settings with an in-tmp database and console logging."""

    return CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=None, model="fake-model", system_prompt="Test system."),
        log=LogSettings(format="console", level="DEBUG"),
    )


@pytest.fixture
async def engine(db_url: str) -> AsyncIterator[AsyncEngine]:
    """A live, migrated async engine pointing at the temp DB."""

    upgrade_to_head(db_url)
    eng = create_engine(db_url)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def fake_gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def gateway_protocol(fake_gateway: FakeGateway) -> LLMGateway:
    """Returns the fake typed as LLMGateway."""

    return fake_gateway


class AllowAllPolicy:
    """Permissive policy used in route tests that exercise the happy path."""

    def evaluate(self, call: ServiceCall) -> PolicyDecision:
        return PolicyDecision(allowed=True, reason="test: allow-all")


@pytest.fixture
def allow_all_policy() -> Policy:
    return AllowAllPolicy()


@pytest.fixture
def ha_service_calls() -> list[dict[str, Any]]:
    """Captures every service call POSTed at the mock HA app."""

    return []


@pytest.fixture
def mock_ha_states() -> dict[str, dict[str, Any]]:
    return {
        "light.kitchen": {
            "entity_id": "light.kitchen",
            "state": "off",
            "attributes": {"friendly_name": "Kitchen Light"},
            "last_changed": "2026-05-16T00:00:00+00:00",
            "last_updated": "2026-05-16T00:00:00+00:00",
        },
    }


@pytest.fixture
async def mock_ha(
    mock_ha_states: dict[str, dict[str, Any]],
    ha_service_calls: list[dict[str, Any]],
) -> AsyncIterator[HAClient]:
    """An ``HAClient`` wired to an in-process FastAPI HA mock."""

    ha_app = make_rest_app(states=mock_ha_states, record=ha_service_calls)
    transport = httpx.ASGITransport(app=ha_app)
    http = httpx.AsyncClient(
        base_url="http://ha.test",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        transport=transport,
    )
    client = HAClient(url="http://ha.test", token=VALID_TOKEN, http=http)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def client(
    settings: CaesarSettings,
    engine: AsyncEngine,
    fake_gateway: FakeGateway,
) -> AsyncIterator[AsyncClient]:
    """An httpx client speaking to a Praetor app with HA *unconfigured*."""

    from caesar.praetor.app import create_app

    app = create_app(settings=settings, gateway=fake_gateway, engine=engine)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


@pytest.fixture
async def client_with_ha(
    settings: CaesarSettings,
    engine: AsyncEngine,
    fake_gateway: FakeGateway,
    mock_ha: HAClient,
    allow_all_policy: Policy,
) -> AsyncIterator[AsyncClient]:
    """An httpx client speaking to a Praetor app with HA *configured*."""

    from caesar.praetor.app import create_app

    app = create_app(
        settings=settings,
        gateway=fake_gateway,
        engine=engine,
        ha=mock_ha,
        policy=allow_all_policy,
    )
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


@pytest.fixture(scope="session")
def nats_url() -> Iterator[str]:
    """A live nats-server URL for the test session.

    Skips the whole module if the binary isn't on PATH. CI installs
    nats-server explicitly; local devs can install via `brew install
    nats-server`, `scoop install nats-server`, etc.
    """

    binary = find_nats_binary()
    if binary is None:
        pytest.skip("nats-server not on PATH; install it to run bus tests.")
    yield from spawn_nats(binary)


@pytest.fixture
async def bus(nats_url: str) -> AsyncIterator[Bus]:
    """A connected Bus pointing at the session's nats-server."""

    b = Bus(nats_url)
    await b.connect()
    try:
        yield b
    finally:
        await b.close()


@pytest.fixture
async def registry(bus: Bus) -> AsyncIterator[WorkerRegistry]:
    """A started WorkerRegistry bound to the test bus."""

    r = WorkerRegistry(bus)
    await r.start()
    try:
        yield r
    finally:
        await r.stop()


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Make sure caching from one test doesn't leak into another."""

    from caesar.config import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """Clear structlog + stdlib logging handlers between tests.

    Without this, configure_logging() in one test binds a handler to
    pytest's captured sys.stdout; the next test's capsys closes that
    buffer, and any lingering log emission (lifespan shutdown logs
    fired by a background task) writes to a closed file. That shows
    up as 'I/O operation on closed file' warnings that pytest's
    filterwarnings=error promotes to failures.
    """

    import logging

    import structlog

    yield
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
