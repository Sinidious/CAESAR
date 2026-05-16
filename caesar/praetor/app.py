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
from caesar.ha.client import HAClient
from caesar.llm.anthropic import AnthropicProvider
from caesar.llm.gateway import LLMGateway
from caesar.log import configure_logging, get_logger
from caesar.policy.engine import DenyAllPolicy, Policy
from caesar.praetor.middleware import request_id_middleware
from caesar.praetor.routes import chat, devices, health


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


def _default_ha(settings: CaesarSettings) -> HAClient | None:
    if settings.ha.url is None or settings.ha.token is None:
        return None
    return HAClient(
        url=settings.ha.url,
        token=settings.ha.token.get_secret_value(),
        timeout=settings.ha.timeout_seconds,
        verify_ssl=settings.ha.verify_ssl,
    )


def create_app(
    *,
    settings: CaesarSettings | None = None,
    gateway: LLMGateway | None = None,
    engine: AsyncEngine | None = None,
    ha: HAClient | None = None,
    policy: Policy | None = None,
) -> FastAPI:
    """Build a Praetor FastAPI app, optionally with injected collaborators."""

    settings = settings or get_settings()
    configure_logging(settings.log)
    logger = get_logger("caesar.praetor")

    engine = engine if engine is not None else create_engine(settings.db.url, echo=settings.db.echo)
    gateway = gateway if gateway is not None else _default_gateway(settings)
    ha = ha if ha is not None else _default_ha(settings)
    policy = policy if policy is not None else DenyAllPolicy()
    audit = AuditLogger(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "praetor.startup",
            version=__version__,
            model=settings.llm.model,
            ha_configured=ha is not None,
            policy=type(policy).__name__,
        )
        try:
            yield
        finally:
            if ha is not None:
                await ha.aclose()
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
    app.state.ha = ha
    app.state.policy = policy
    app.state.audit = audit

    app.middleware("http")(request_id_middleware)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(devices.router)
    return app
