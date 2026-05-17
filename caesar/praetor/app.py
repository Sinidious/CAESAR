"""FastAPI app factory (ADR-0006).

The factory takes optional overrides so tests can inject a fake
gateway / engine / settings without monkeypatching globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar import __version__
from caesar.bus.client import Bus
from caesar.config import CaesarSettings, get_settings
from caesar.db.audit import AuditLogger
from caesar.db.engine import create_engine
from caesar.ha.client import HAClient
from caesar.legion.memory_recall import MemoryRecallWorker
from caesar.legion.registry import WorkerRegistry
from caesar.legion.worker import Worker
from caesar.llm.anthropic import AnthropicProvider
from caesar.llm.gateway import LLMGateway
from caesar.log import configure_logging, get_logger
from caesar.memory.retention import RetentionSweeper
from caesar.policy.allowlist import AllowlistPolicy
from caesar.policy.engine import DenyAllPolicy, Policy
from caesar.policy.yaml_loader import load_rules
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


def _default_bus(settings: CaesarSettings) -> Bus | None:
    """Construct the Bus when CAESAR_BUS__ENABLED is true; else ``None``."""

    if not settings.bus.enabled:
        return None
    return Bus(settings.bus.url, connect_timeout=settings.bus.connect_timeout)


def _build_inprocess_worker(
    name: str,
    *,
    bus: Bus,
    engine: AsyncEngine,
    settings: CaesarSettings,
) -> Worker:
    """Construct one of the in-process workers from its config name."""

    if name == "memory_recall":
        return MemoryRecallWorker(
            bus,
            engine,
            default_limit=settings.legion.recall_default_limit,
            max_limit=settings.legion.recall_max_limit,
        )
    raise ValueError(f"unknown in-process worker: {name!r}")


def _default_policy(settings: CaesarSettings) -> Policy:
    """Build the configured Policy, or fall back to the deny-all stub.

    When ``CAESAR_POLICY__RULES_PATH`` is set, load it now and raise
    ``PolicyRulesError`` on any problem — the operator should learn
    about a broken rules file at startup, not at the first service call.
    """

    if settings.policy.rules_path is None:
        return DenyAllPolicy()
    rules = load_rules(settings.policy.rules_path)
    return AllowlistPolicy(rules)


def create_app(
    *,
    settings: CaesarSettings | None = None,
    gateway: LLMGateway | None = None,
    engine: AsyncEngine | None = None,
    ha: HAClient | None = None,
    policy: Policy | None = None,
    bus: Bus | None = None,
) -> FastAPI:
    """Build a Praetor FastAPI app, optionally with injected collaborators."""

    settings = settings or get_settings()
    configure_logging(settings.log)
    logger = get_logger("caesar.praetor")

    engine = engine if engine is not None else create_engine(settings.db.url, echo=settings.db.echo)
    gateway = gateway if gateway is not None else _default_gateway(settings)
    ha = ha if ha is not None else _default_ha(settings)
    policy = policy if policy is not None else _default_policy(settings)
    bus = bus if bus is not None else _default_bus(settings)
    registry = WorkerRegistry(bus) if bus is not None else None
    audit = AuditLogger(engine)
    sweeper = RetentionSweeper(
        engine,
        audit,
        retention_days=settings.memory.retention_days,
        interval_seconds=settings.memory.sweep_interval_seconds,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        workers: list[Worker] = []
        if bus is not None:
            await bus.connect()
        if registry is not None:
            await registry.start()
        if bus is not None:
            for name in settings.legion.inprocess_workers:
                worker = _build_inprocess_worker(name, bus=bus, engine=engine, settings=settings)
                await worker.start()
                workers.append(worker)
        elif settings.legion.inprocess_workers:
            logger.warning(
                "praetor.inprocess_workers_skipped",
                reason="bus disabled",
                workers=settings.legion.inprocess_workers,
            )
        sweeper.start_background()
        logger.info(
            "praetor.startup",
            version=__version__,
            model=settings.llm.model,
            ha_configured=ha is not None,
            policy=type(policy).__name__,
            bus_enabled=bus is not None,
            inprocess_workers=[w.worker_id for w in workers],
            retention_days=settings.memory.retention_days,
        )

        async def _safe(coro_fn: Callable[[], Awaitable[None]], what: str) -> None:
            try:
                await coro_fn()
            except Exception as exc:  # don't let one shutdown step skip the rest
                logger.warning("praetor.shutdown.step_failed", step=what, error=str(exc))

        try:
            yield
        finally:
            await _safe(sweeper.stop_background, "sweeper")
            for worker in reversed(workers):
                await _safe(worker.stop, f"worker:{worker.worker_id}")
            if registry is not None:
                await _safe(registry.stop, "registry")
            if bus is not None:
                await _safe(bus.close, "bus")
            if ha is not None:
                await _safe(ha.aclose, "ha")
            await _safe(engine.dispose, "engine")
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
    app.state.bus = bus
    app.state.registry = registry
    app.state.sweeper = sweeper

    app.middleware("http")(request_id_middleware)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(devices.router)
    return app
