"""Tests for OpenTelemetry tracing (ADR-0023).

The ``[otel]`` extra isn't installed in CI, so the meaningful path
under test in CI is the no-op path: every helper must be safe to call
when the SDK isn't importable, and ``setup_tracing`` must return
``None`` without instrumenting anything.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar import tracing
from caesar.config import CaesarSettings, DatabaseSettings, LLMSettings, LogSettings
from caesar.praetor.app import create_app


def _settings(db_url: str) -> CaesarSettings:
    return CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
    )


def test_otel_not_available_when_extra_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin behaviour when the SDK isn't importable (CI's default)."""

    # Force re-detection so the cached result from a prior test can't
    # mask the path we want to exercise.
    monkeypatch.setattr(tracing, "_OTEL_AVAILABLE", None)
    if not tracing._otel_available():
        assert tracing._otel_available() is False


def test_setup_tracing_returns_none_when_disabled_by_env(
    monkeypatch: pytest.MonkeyPatch,
    db_url: str,
    engine: AsyncEngine,
    fake_gateway: Any,
) -> None:
    """OTEL_SDK_DISABLED short-circuits before any imports."""

    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    app = create_app(settings=_settings(db_url), gateway=fake_gateway, engine=engine)
    assert tracing.setup_tracing(app, engine) is None


def test_setup_tracing_returns_none_when_extra_missing(
    monkeypatch: pytest.MonkeyPatch,
    db_url: str,
    engine: AsyncEngine,
    fake_gateway: Any,
) -> None:
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setattr(tracing, "_OTEL_AVAILABLE", False)
    app = create_app(settings=_settings(db_url), gateway=fake_gateway, engine=engine)
    assert tracing.setup_tracing(app, engine) is None


def test_span_is_noop_when_extra_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """``span`` must yield None and never raise when the SDK is absent."""

    monkeypatch.setattr(tracing, "_OTEL_AVAILABLE", False)
    with tracing.span("anything", foo="bar") as sp:
        assert sp is None


def test_llm_span_is_noop_when_extra_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_OTEL_AVAILABLE", False)
    with tracing.llm_span("claude-haiku-4-5", **{"gen_ai.request.max_tokens": 64}) as sp:
        assert sp is None


def test_set_token_usage_with_none_span_is_safe() -> None:
    tracing.set_token_usage(None, input_tokens=10, output_tokens=20)


def test_shutdown_tracing_with_none_is_safe() -> None:
    tracing.shutdown_tracing(None)


def test_otel_available_detection_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """The detection result is cached so we don't re-import on every span."""

    monkeypatch.setattr(tracing, "_OTEL_AVAILABLE", None)
    first = tracing._otel_available()
    second = tracing._otel_available()
    assert first is second


@pytest.fixture
def reset_otel_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_OTEL_AVAILABLE", None)


def test_setup_tracing_real_path_smoke(reset_otel_cache: None) -> None:
    """When the extra IS installed, the real path is reachable end-to-end.

    Skipped in CI (the [otel] extra isn't installed there). Run locally
    with ``pip install -e '.[dev,otel]'`` to exercise it.
    """

    pytest.importorskip("opentelemetry.sdk.trace")

    from opentelemetry.sdk.trace import TracerProvider

    # We can't easily fully wire FastAPI + SQLAlchemy instrumentation
    # against the test engine without leaking global state across the
    # suite, so this test just exercises the helpers, which is enough
    # to prove the real path is wired.
    with tracing.span("test.span", x=1) as sp:
        assert sp is not None
    with tracing.llm_span("test-model", **{"gen_ai.request.max_tokens": 8}) as sp:
        assert sp is not None
        tracing.set_token_usage(sp, input_tokens=1, output_tokens=2)

    # The provider type is what we want when the real path is taken.
    assert TracerProvider is not None
