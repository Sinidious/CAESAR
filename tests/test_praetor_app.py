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
