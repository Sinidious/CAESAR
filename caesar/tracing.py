"""OpenTelemetry tracing wiring (ADR-0023).

Tracing is opt-in: install ``caesar[otel]`` to pull the SDK and
instrumentations. Without the extra, :func:`setup_tracing` returns
``None`` after logging a single ``tracing.disabled`` debug line. With
the extra installed, it:

- Configures the global ``TracerProvider`` with a service-name
  resource, ``ParentBased(AlwaysOn)`` sampler, and an OTLP/HTTP
  exporter (env-tunable via ``OTEL_*``).
- Instruments the FastAPI app and the async SQLAlchemy engine.
- Exposes :func:`span` and :func:`llm_span` helpers for callers that
  want to add custom spans (brain graph nodes, Anthropic SDK calls)
  without conditional imports at the call site.

All ``OTEL_*`` env vars from the OTel spec are honoured directly; we
don't introduce CAESAR-specific aliases.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from caesar import __version__
from caesar.log import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = get_logger("caesar.tracing")

_OTEL_AVAILABLE: bool | None = None


def _otel_available() -> bool:
    """Return True iff the ``[otel]`` extra is importable."""

    global _OTEL_AVAILABLE
    if _OTEL_AVAILABLE is None:
        try:
            import opentelemetry.sdk.trace  # noqa: F401

            _OTEL_AVAILABLE = True
        except ImportError:
            _OTEL_AVAILABLE = False
    return _OTEL_AVAILABLE


def _env_enabled() -> bool:
    """Honour ``OTEL_SDK_DISABLED`` (OTel spec)."""

    return os.environ.get("OTEL_SDK_DISABLED", "").lower() not in {"1", "true", "yes"}


def setup_tracing(app: FastAPI, engine: AsyncEngine) -> Any | None:
    """Initialise the OTel SDK and instrument ``app`` + ``engine``.

    Returns the configured ``TracerProvider`` so the caller can keep a
    reference for shutdown; returns ``None`` when tracing is disabled
    (extra missing or env-disabled).
    """

    if not _env_enabled():
        logger.debug("tracing.disabled", reason="OTEL_SDK_DISABLED")
        return None
    if not _otel_available():
        logger.debug("tracing.disabled", reason="extra-not-installed")
        return None

    return _setup_tracing_real(app, engine)  # pragma: no cover - requires [otel] extra


def _setup_tracing_real(
    app: FastAPI, engine: AsyncEngine
) -> Any:  # pragma: no cover - requires [otel] extra
    """Real OTel wiring; only reached when the extra is installed."""

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased

    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "caesar-praetor"),
            "service.version": __version__,
        }
    )
    provider = TracerProvider(resource=resource, sampler=ParentBased(ALWAYS_ON))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)

    logger.info(
        "tracing.enabled",
        endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"),
    )
    return provider


def shutdown_tracing(provider: Any | None) -> None:
    """Flush + shut down the configured provider, if any."""

    if provider is None:
        return
    with contextlib.suppress(Exception):  # pragma: no cover - defensive
        provider.shutdown()


@contextlib.contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Start a span named ``name``; no-op when tracing is disabled.

    Usage::

        with span("brain.node.call_llm", iteration=2):
            ...
    """

    if not _otel_available():
        yield None
        return
    from opentelemetry import trace  # pragma: no cover - requires [otel] extra

    tracer = trace.get_tracer("caesar")  # pragma: no cover - requires [otel] extra
    with tracer.start_as_current_span(  # pragma: no cover - requires [otel] extra
        name, attributes=attributes
    ) as sp:
        yield sp


@contextlib.contextmanager
def llm_span(model: str, **attributes: Any) -> Iterator[Any]:
    """Start a GenAI span around an LLM SDK call.

    Sets the OTel GenAI semantic-convention attributes so backends with
    a GenAI view (Grafana, SigNoz) light up automatically.
    """

    base = {"gen_ai.system": "anthropic", "gen_ai.request.model": model}
    base.update(attributes)
    with span("llm.complete", **base) as sp:
        yield sp


def set_token_usage(sp: Any | None, *, input_tokens: int, output_tokens: int) -> None:
    """Decorate ``sp`` (if any) with GenAI usage attributes."""

    if sp is None:
        return
    sp.set_attribute(
        "gen_ai.usage.input_tokens", input_tokens
    )  # pragma: no cover - requires [otel] extra
    sp.set_attribute(
        "gen_ai.usage.output_tokens", output_tokens
    )  # pragma: no cover - requires [otel] extra
