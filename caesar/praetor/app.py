"""FastAPI app factory (ADR-0006).

The factory takes optional overrides so tests can inject a fake
gateway / engine / settings without monkeypatching globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar import __version__
from caesar.config import CaesarSettings, get_settings
from caesar.db.audit import AuditLogger
from caesar.db.engine import create_engine
from caesar.llm.anthropic import AnthropicProvider
from caesar.llm.gateway import LLMGateway
from caesar.log import configure_logging, get_logger
from caesar.praetor.middleware import request_id_middleware
from caesar.praetor.routes import chat, health


def _default_gateway(settings: CaesarSettings) -> LLMGateway:
    if settings.llm.api_key is None:
        raise RuntimeError(
            "CAESAR_LLM__API_KEY is required when no gateway is injected.",
        )
    return AnthropicProvider(
        api_key=settings.llm.api_key.get_secret_value(),
        default_model=settings.llm.model,
        default_max_tokens=settings.llm.max_tokens,
    )


def create_app(
    *,
    settings: CaesarSettings | None = None,
    gateway: LLMGateway | None = None,
    engine: AsyncEngine | None = None,
) -> FastAPI:
    """Build a Praetor FastAPI app, optionally with injected collaborators."""

    settings = settings or get_settings()
    configure_logging(settings.log)
    logger = get_logger("caesar.praetor")

    engine = engine if engine is not None else create_engine(settings.db.url, echo=settings.db.echo)
    gateway = gateway if gateway is not None else _default_gateway(settings)
    audit = AuditLogger(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "praetor.startup",
            version=__version__,
            model=settings.llm.model,
        )
        try:
            yield
        finally:
            await engine.dispose()
            logger.info("praetor.shutdown")

    app = FastAPI(
        title="CAESAR Praetor",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.gateway = gateway
    app.state.audit = audit

    app.middleware("http")(request_id_middleware)
    app.include_router(health.router)
    app.include_router(chat.router)
    return app
