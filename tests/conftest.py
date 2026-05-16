"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.config import CaesarSettings, DatabaseSettings, LLMSettings, LogSettings
from caesar.db.engine import create_engine
from caesar.db.migrate import upgrade_to_head
from caesar.llm.gateway import ChatMessage, ChatResponse, LLMGateway


class FakeGateway:
    """A deterministic LLMGateway for tests.

    Records every call and returns a canned reply. Implements the
    Protocol structurally so type checkers accept it where an
    ``LLMGateway`` is required.
    """

    def __init__(self, reply: str = "hello back") -> None:
        self.reply = reply
        self.calls: list[dict[str, object]] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        self.calls.append(
            {
                "messages": messages,
                "system": system,
                "model": model,
                "max_tokens": max_tokens,
            }
        )
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


@pytest.fixture
async def client(
    settings: CaesarSettings,
    engine: AsyncEngine,
    fake_gateway: FakeGateway,
) -> AsyncIterator[AsyncClient]:
    """An httpx client speaking to a fully-wired Praetor app."""

    from caesar.praetor.app import create_app

    app = create_app(settings=settings, gateway=fake_gateway, engine=engine)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Make sure caching from one test doesn't leak into another."""

    from caesar.config import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()
