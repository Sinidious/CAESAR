from __future__ import annotations

import pytest

from caesar.config import (
    CaesarSettings,
    get_settings,
    reset_settings_cache,
)


def test_defaults_are_safe():
    s = CaesarSettings()
    assert s.db.url.startswith("sqlite+aiosqlite:///")
    assert s.llm.model.startswith("claude-")
    assert s.log.format == "json"
    assert s.server.host == "127.0.0.1"
    assert s.server.port == 8000
    assert s.llm.api_key is None
    # SR-007: dashboard sessions expire after 7 days by default.
    assert s.dashboard.cookie_max_age_seconds == 60 * 60 * 24 * 7


def test_env_nested_delimiter(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAESAR_LLM__MODEL", "claude-test")
    monkeypatch.setenv("CAESAR_LLM__API_KEY", "sk-test")
    monkeypatch.setenv("CAESAR_DB__URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("CAESAR_LOG__FORMAT", "console")
    monkeypatch.setenv("CAESAR_SERVER__PORT", "9000")

    s = CaesarSettings()
    assert s.llm.model == "claude-test"
    assert s.llm.api_key is not None
    assert s.llm.api_key.get_secret_value() == "sk-test"
    assert s.db.url == "sqlite+aiosqlite:///:memory:"
    assert s.log.format == "console"
    assert s.server.port == 9000


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch):
    reset_settings_cache()
    monkeypatch.setenv("CAESAR_LLM__MODEL", "claude-first")
    first = get_settings()
    monkeypatch.setenv("CAESAR_LLM__MODEL", "claude-second")
    second = get_settings()
    assert first is second
    reset_settings_cache()
    third = get_settings()
    assert third is not first
    assert third.llm.model == "claude-second"
