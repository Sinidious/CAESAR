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
