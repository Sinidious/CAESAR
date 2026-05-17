"""FastAPI app factory (ADR-0006).

The factory takes optional overrides so tests can inject a fake
gateway / engine / settings without monkeypatching globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar import __version__
from caesar.bus.client import Bus, BusAuth
from caesar.config import CaesarSettings, LLMProvider, get_settings
from caesar.db.audit import AuditLogger
from caesar.db.engine import create_engine
from caesar.db.settings_store import SettingsStore
from caesar.ha.client import HAClient
from caesar.legion.memory_recall import MemoryRecallWorker
from caesar.legion.registry import WorkerRegistry
from caesar.legion.semantic_recall import SemanticRecallWorker
from caesar.legion.worker import Worker
from caesar.llm.anthropic import AnthropicProvider
from caesar.llm.embeddings import Embedder, StubEmbedder, VoyageEmbedder
from caesar.llm.gateway import LLMGateway
from caesar.llm.ollama import OllamaProvider
from caesar.llm.openai import OpenAIProvider
from caesar.llm.router import TaskRouter
from caesar.log import configure_logging, get_logger
from caesar.memory.retention import RetentionSweeper
from caesar.memory.semantic import SemanticIndexer
from caesar.metrics import register_app_collector, unregister_collector
from caesar.policy.allowlist import AllowlistPolicy
from caesar.policy.engine import DenyAllPolicy, Policy
from caesar.policy.yaml_loader import load_rules
from caesar.praetor.audit_bus import AuditEventBus
from caesar.praetor.dashboard import build_router as build_dashboard_router
from caesar.praetor.dashboard.rate_limit import LoginRateLimiter
from caesar.praetor.dashboard.routes import STATIC_DIR as DASHBOARD_STATIC_DIR
from caesar.praetor.dashboard.security_headers import dashboard_security_headers_middleware
from caesar.praetor.middleware import request_id_middleware
from caesar.praetor.routes import chat, devices, health
from caesar.praetor.routes import metrics as metrics_route
from caesar.tracing import setup_tracing, shutdown_tracing


def _build_provider(
    provider: LLMProvider,
    *,
    model: str,
    settings: CaesarSettings,
) -> LLMGateway:
    """Construct one provider gateway from its sub-settings."""

    if provider == "anthropic":
        api_key = settings.llm.anthropic.api_key or settings.llm.api_key
        if api_key is None:
            raise RuntimeError(
                "CAESAR_LLM__ANTHROPIC__API_KEY (or legacy CAESAR_LLM__API_KEY) "
                "is required when provider=anthropic and no gateway is injected.",
            )
        return AnthropicProvider(
            api_key=api_key.get_secret_value(),
            default_model=model,
            default_max_tokens=settings.llm.max_tokens,
        )
    if provider == "openai":
        if settings.llm.openai.api_key is None:
            raise RuntimeError(
                "CAESAR_LLM__OPENAI__API_KEY is required when provider=openai.",
            )
        return OpenAIProvider(
            api_key=settings.llm.openai.api_key.get_secret_value(),
            default_model=model,
            default_max_tokens=settings.llm.max_tokens,
            base_url=settings.llm.openai.base_url,
        )
    if provider == "ollama":
        return OllamaProvider(
            default_model=model,
            default_max_tokens=settings.llm.max_tokens,
            base_url=settings.llm.ollama.base_url,
        )
    raise RuntimeError(  # pragma: no cover - exhaustively matched above
        f"unknown LLM provider: {provider!r}",
    )


def _default_gateway(settings: CaesarSettings) -> LLMGateway:
    """Construct the gateway router (ADR-0026).

    Builds one provider gateway for the configured default, plus one
    per entry in ``settings.llm.task_routing``. The router wraps them
    so callers pass ``task="<name>"`` and the right provider answers.
    With an empty ``task_routing`` dict (the default) every task is
    served by the configured default — same behaviour as v1.0.
    """

    default = _build_provider(
        settings.llm.provider,
        model=settings.llm.model,
        settings=settings,
    )
    per_task: dict[str, LLMGateway] = {}
    for task_name, task_cfg in settings.llm.task_routing.items():
        per_task[task_name] = _build_provider(
            task_cfg.provider,
            model=task_cfg.model,
            settings=settings,
        )
    return TaskRouter(default=default, per_task=per_task)


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
    """Construct the Bus when CAESAR_BUS__ENABLED is true; else ``None``.

    When ``CAESAR_BUS__AUTH__ENABLED`` is also set (ADR-0027), the
    bus is constructed with the NKEY signing material so cross-host
    workers authenticate.
    """

    if not settings.bus.enabled:
        return None
    auth: BusAuth | None = None
    if settings.bus.auth.enabled:
        auth = BusAuth(
            nkey_seed=(
                settings.bus.auth.nkey_seed.get_secret_value()
                if settings.bus.auth.nkey_seed is not None
                else None
            ),
            nkey_seed_path=settings.bus.auth.nkey_seed_path,
            user=settings.bus.auth.user,
        )
    return Bus(
        settings.bus.url,
        connect_timeout=settings.bus.connect_timeout,
        auth=auth,
    )


def _default_embedder(settings: CaesarSettings) -> Embedder:
    """Return the configured Embedder. Voyage when an API key is set;
    StubEmbedder otherwise so dev / test runs work without one."""

    if settings.semantic.voyage_api_key is not None:
        return VoyageEmbedder(
            api_key=settings.semantic.voyage_api_key.get_secret_value(),
            model=settings.semantic.model,
            dimension=settings.semantic.embedding_dim,
        )
    return StubEmbedder(dimension=settings.semantic.embedding_dim, model="stub-embedder")


def _build_inprocess_worker(
    name: str,
    *,
    bus: Bus,
    engine: AsyncEngine,
    settings: CaesarSettings,
    embedder: Embedder | None,
) -> Worker:
    """Construct one of the in-process workers from its config name."""

    if name == "memory_recall":
        return MemoryRecallWorker(
            bus,
            engine,
            default_limit=settings.legion.recall_default_limit,
            max_limit=settings.legion.recall_max_limit,
        )
    if name == "semantic_recall":
        if embedder is None:
            raise ValueError("semantic_recall worker requires CAESAR_SEMANTIC__ENABLED=true")
        return SemanticRecallWorker(
            bus,
            engine,
            embedder,
            default_limit=settings.semantic.top_k_default,
            max_limit=settings.semantic.top_k_max,
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
    audit_bus = AuditEventBus()
    audit = AuditLogger(
        engine,
        bus=audit_bus,
        max_string_chars=settings.memory.audit_max_string_chars,
    )
    settings_store = SettingsStore(engine)
    registry = WorkerRegistry(bus, audit=audit) if bus is not None else None
    sweeper = RetentionSweeper(
        engine,
        audit,
        retention_days=settings.memory.retention_days,
        interval_seconds=settings.memory.sweep_interval_seconds,
    )
    embedder: Embedder | None = _default_embedder(settings) if settings.semantic.enabled else None
    semantic_indexer: SemanticIndexer | None = None
    if embedder is not None:
        semantic_indexer = SemanticIndexer(
            engine,
            embedder,
            event_types=settings.semantic.indexed_event_types,
            interval_seconds=settings.semantic.indexer_interval_seconds,
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
                worker = _build_inprocess_worker(
                    name, bus=bus, engine=engine, settings=settings, embedder=embedder
                )
                await worker.start()
                workers.append(worker)
        elif settings.legion.inprocess_workers:
            logger.warning(
                "praetor.inprocess_workers_skipped",
                reason="bus disabled",
                workers=settings.legion.inprocess_workers,
            )
        sweeper.start_background()
        if semantic_indexer is not None:
            semantic_indexer.start_background()
        app.state.metrics_collector = register_app_collector(app)
        app.state.tracing_provider = setup_tracing(app, engine)
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

        try:
            yield
        finally:
            if semantic_indexer is not None:
                await semantic_indexer.stop_background()
            await sweeper.stop_background()
            for worker in reversed(workers):
                await worker.stop()
            if registry is not None:
                await registry.stop()
            if bus is not None:
                await bus.close()
            if ha is not None:
                await ha.aclose()
            shutdown_tracing(getattr(app.state, "tracing_provider", None))
            await engine.dispose()
            collector = getattr(app.state, "metrics_collector", None)
            if collector is not None:
                unregister_collector(collector)
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
    app.state.embedder = embedder
    app.state.semantic_indexer = semantic_indexer
    app.state.audit_bus = audit_bus
    app.state.settings_store = settings_store
    app.state.login_rate_limiter = LoginRateLimiter()

    app.middleware("http")(request_id_middleware)
    app.middleware("http")(dashboard_security_headers_middleware)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(devices.router)
    app.include_router(metrics_route.router)
    app.state.metrics_collector = None
    if settings.dashboard.token is not None:
        app.include_router(build_dashboard_router())
        app.mount(
            "/dashboard/static",
            StaticFiles(directory=DASHBOARD_STATIC_DIR),
            name="dashboard-static",
        )
    return app
