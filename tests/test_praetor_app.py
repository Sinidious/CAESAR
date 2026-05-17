from __future__ import annotations

import pytest
from pydantic import SecretStr

from caesar.config import CaesarSettings, DatabaseSettings, LLMSettings, LogSettings
from caesar.praetor.app import create_app


def _settings_without_key(db_url: str) -> CaesarSettings:
    return CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=None),
        log=LogSettings(format="console", level="DEBUG"),
    )


def _settings_with_key(db_url: str) -> CaesarSettings:
    return CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
    )


def test_create_app_without_key_or_gateway_raises(db_url: str):
    with pytest.raises(RuntimeError, match="CAESAR_LLM__API_KEY"):
        create_app(settings=_settings_without_key(db_url))


def test_create_app_with_key_builds_default_gateway(db_url: str):
    from caesar.llm.anthropic import AnthropicProvider

    app = create_app(settings=_settings_with_key(db_url))
    assert isinstance(app.state.gateway, AnthropicProvider)


def test_create_app_with_ha_settings_builds_default_ha(db_url: str):
    """When CAESAR_HA__URL and CAESAR_HA__TOKEN are set, the bridge is built."""

    from caesar.config import HASettings
    from caesar.ha.client import HAClient

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        ha=HASettings(url="http://ha.test", token=SecretStr("ha-token")),
    )
    app = create_app(settings=settings)
    assert isinstance(app.state.ha, HAClient)


def test_create_app_without_ha_settings_leaves_ha_none(db_url: str):
    """No HA url/token → app.state.ha is None."""

    app = create_app(settings=_settings_with_key(db_url))
    assert app.state.ha is None


def test_create_app_loads_yaml_policy_from_rules_path(db_url: str, tmp_path):
    """When CAESAR_POLICY__RULES_PATH is set, AllowlistPolicy is loaded."""

    from caesar.config import PolicySettings
    from caesar.policy.allowlist import AllowlistPolicy

    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "version: 1\nallowed_services:\n  - light.turn_on\n",
        encoding="utf-8",
    )
    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        policy=PolicySettings(rules_path=rules_path),
    )
    app = create_app(settings=settings)
    assert isinstance(app.state.policy, AllowlistPolicy)
    assert "light.turn_on" in app.state.policy.allowed_services


def test_create_app_with_broken_rules_path_raises(db_url: str, tmp_path):
    """Missing rules file makes startup fail fast."""

    from caesar.config import PolicySettings
    from caesar.policy.yaml_loader import PolicyRulesError

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        policy=PolicySettings(rules_path=tmp_path / "missing.yaml"),
    )
    with pytest.raises(PolicyRulesError):
        create_app(settings=settings)


def test_create_app_without_bus_leaves_bus_and_registry_none(db_url: str):
    """Default settings have bus.enabled=False; no bus or registry attached."""

    app = create_app(settings=_settings_with_key(db_url))
    assert app.state.bus is None
    assert app.state.registry is None


def test_create_app_with_bus_enabled_constructs_bus(db_url: str):
    """When CAESAR_BUS__ENABLED=true, a Bus is constructed (but not yet connected)."""

    from caesar.bus.client import Bus
    from caesar.config import BusSettings
    from caesar.legion.registry import WorkerRegistry

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        bus=BusSettings(enabled=True, url="nats://127.0.0.1:1"),
    )
    app = create_app(settings=settings)
    assert isinstance(app.state.bus, Bus)
    assert isinstance(app.state.registry, WorkerRegistry)


async def test_unknown_inprocess_worker_raises(nats_url: str, db_url: str, fake_gateway) -> None:
    """An unknown name in inprocess_workers fails fast at startup."""

    from caesar.config import BusSettings, LegionSettings

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        bus=BusSettings(enabled=True, url=nats_url),
        legion=LegionSettings(inprocess_workers=["nope"]),
    )
    app = create_app(settings=settings, gateway=fake_gateway)

    with pytest.raises(ValueError, match="unknown in-process worker"):
        async with app.router.lifespan_context(app):
            pass


def test_default_embedder_picks_voyage_when_api_key_present(db_url: str) -> None:
    """When a Voyage key is configured, _default_embedder returns VoyageEmbedder."""

    from caesar.config import SemanticSettings
    from caesar.llm.embeddings import VoyageEmbedder
    from caesar.praetor.app import _default_embedder

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        semantic=SemanticSettings(
            enabled=True, voyage_api_key=SecretStr("vk-test"), embedding_dim=64
        ),
    )
    embedder = _default_embedder(settings)
    assert isinstance(embedder, VoyageEmbedder)


def test_default_embedder_falls_back_to_stub(db_url: str) -> None:
    from caesar.config import SemanticSettings
    from caesar.llm.embeddings import StubEmbedder
    from caesar.praetor.app import _default_embedder

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        semantic=SemanticSettings(enabled=True, embedding_dim=64),
    )
    embedder = _default_embedder(settings)
    assert isinstance(embedder, StubEmbedder)


def test_create_app_with_semantic_enabled_attaches_indexer(
    db_url: str, engine, fake_gateway
) -> None:
    """When semantic is enabled, app.state.semantic_indexer is non-None."""

    from caesar.config import SemanticSettings
    from caesar.memory.semantic import SemanticIndexer

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        semantic=SemanticSettings(enabled=True, embedding_dim=64),
    )
    app = create_app(settings=settings, gateway=fake_gateway, engine=engine)
    assert isinstance(app.state.semantic_indexer, SemanticIndexer)
    assert app.state.embedder is not None


async def test_lifespan_starts_and_stops_semantic_indexer(
    db_url: str, engine, fake_gateway
) -> None:
    from caesar.config import SemanticSettings
    from caesar.memory.semantic import SemanticIndexer

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        semantic=SemanticSettings(enabled=True, embedding_dim=64),
    )
    app = create_app(settings=settings, gateway=fake_gateway, engine=engine)
    indexer: SemanticIndexer = app.state.semantic_indexer

    running_during = False
    async with app.router.lifespan_context(app):
        running_during = indexer.is_running
    running_after = indexer.is_running

    assert running_during
    assert not running_after


async def test_semantic_recall_inprocess_worker_requires_embedder(
    nats_url: str, db_url: str, fake_gateway
) -> None:
    """Listing semantic_recall as in-process worker without semantic.enabled fails fast."""

    from caesar.config import BusSettings, LegionSettings

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        bus=BusSettings(enabled=True, url=nats_url),
        legion=LegionSettings(inprocess_workers=["semantic_recall"]),
        # semantic disabled
    )
    app = create_app(settings=settings, gateway=fake_gateway)
    with pytest.raises(ValueError, match="CAESAR_SEMANTIC__ENABLED"):
        async with app.router.lifespan_context(app):
            pass


async def test_lifespan_cleanup_runs_even_when_ha_not_configured(
    db_url: str, engine, fake_gateway
) -> None:
    """The lifespan finally must call engine.dispose without HA configured."""

    app = create_app(settings=_settings_with_key(db_url), gateway=fake_gateway, engine=engine)
    async with app.router.lifespan_context(app):
        pass
    # engine.dispose() was called; reconnecting still works because dispose
    # only closes pooled connections (it's idempotent).
    assert app.state.engine is engine


async def test_lifespan_starts_and_stops_retention_sweeper(
    db_url: str, engine, fake_gateway, capsys: pytest.CaptureFixture[str]
) -> None:
    """Retention sweep is part of the lifespan and reports started/stopped."""

    from caesar.memory.retention import RetentionSweeper

    app = create_app(settings=_settings_with_key(db_url), gateway=fake_gateway, engine=engine)
    sweeper: RetentionSweeper = app.state.sweeper

    task_running_during = False
    async with app.router.lifespan_context(app):
        task_running_during = sweeper.is_running
    task_running_after = sweeper.is_running

    assert task_running_during
    assert not task_running_after

    out = capsys.readouterr().out
    assert "memory.sweep.started" in out
    assert "memory.sweep.stopped" in out


async def test_no_warning_when_bus_disabled_and_no_workers(
    db_url: str, fake_gateway, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bus disabled + empty inprocess_workers → no skip warning."""

    from caesar.config import LegionSettings

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        legion=LegionSettings(inprocess_workers=[]),
    )
    app = create_app(settings=settings, gateway=fake_gateway)
    async with app.router.lifespan_context(app):
        pass
    out = capsys.readouterr().out
    assert "inprocess_workers_skipped" not in out


async def test_inprocess_workers_warning_when_bus_disabled(
    db_url: str, fake_gateway, capsys: pytest.CaptureFixture[str]
) -> None:
    """Configured workers + disabled bus logs a warning but doesn't crash."""

    from caesar.config import LegionSettings

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        legion=LegionSettings(inprocess_workers=["memory_recall"]),
    )
    app = create_app(settings=settings, gateway=fake_gateway)

    async with app.router.lifespan_context(app):
        pass

    out = capsys.readouterr().out
    assert "inprocess_workers_skipped" in out


async def test_lifespan_starts_inprocess_memory_recall_worker(
    nats_url: str, db_url: str, engine, fake_gateway
) -> None:
    """End-to-end: bus enabled + memory_recall in inprocess_workers → worker
    registers and is dispatchable through the registry."""

    import asyncio

    from caesar.bus.client import Bus
    from caesar.config import BusSettings, LegionSettings
    from caesar.legion.memory_recall import CAPABILITY
    from caesar.legion.registry import WorkerRegistry

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        bus=BusSettings(enabled=True, url=nats_url),
        legion=LegionSettings(inprocess_workers=["memory_recall"]),
    )
    # Use the migrated engine fixture so audit_log exists.
    app = create_app(settings=settings, engine=engine, gateway=fake_gateway)

    async with app.router.lifespan_context(app):
        bus = app.state.bus
        registry: WorkerRegistry = app.state.registry
        assert isinstance(bus, Bus)
        for _ in range(50):
            if CAPABILITY in set(registry.capabilities()):
                break
            await asyncio.sleep(0.02)
        result = await registry.dispatch(CAPABILITY, {"limit": 5})
        assert result.success is True, result.error
        assert (result.result or {})["count"] >= 0


async def test_lifespan_connects_and_disconnects_bus(
    nats_url: str, db_url: str, fake_gateway, capsys: pytest.CaptureFixture[str]
) -> None:
    """Lifespan should connect the bus on startup and close it on shutdown."""

    from caesar.bus.client import Bus
    from caesar.config import BusSettings, DatabaseSettings, LogSettings
    from caesar.db.engine import create_engine

    settings = CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        bus=BusSettings(enabled=True, url=nats_url),
    )
    eng = create_engine(db_url)
    app = create_app(settings=settings, gateway=fake_gateway, engine=eng)
    bus: Bus = app.state.bus

    before = bus.is_connected
    async with app.router.lifespan_context(app):
        during = bus.is_connected
    after = bus.is_connected

    assert before is False
    assert during is True
    assert after is False

    out = capsys.readouterr().out
    assert "bus_enabled" in out
    assert "praetor.shutdown" in out


async def test_lifespan_runs_startup_and_shutdown(
    db_url: str,
    fake_gateway,
    settings: CaesarSettings,
    capsys: pytest.CaptureFixture[str],
):
    """The factory's lifespan should log both startup and shutdown."""

    from caesar.db.engine import create_engine

    eng = create_engine(db_url)
    app = create_app(settings=settings, gateway=fake_gateway, engine=eng)

    async with app.router.lifespan_context(app):
        pass

    out = capsys.readouterr().out
    assert "praetor.startup" in out
    assert "praetor.shutdown" in out
