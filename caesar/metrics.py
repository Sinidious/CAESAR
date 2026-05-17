"""Prometheus metrics (cross-cutting; mounted by Praetor's /metrics route).

Counters and histograms are module-level singletons so any code path
can ``import`` and ``.inc()`` / ``.observe()`` without plumbing a
registry through.

Gauges that need to be sampled at scrape time (worker count, bus
subscribers) live on :class:`CaesarCollector`, which Praetor
registers with the default registry at app construction.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from prometheus_client import REGISTRY, Counter, Histogram
from prometheus_client.metrics_core import GaugeMetricFamily, Metric

if TYPE_CHECKING:
    from fastapi import FastAPI


AUDIT_EVENTS = Counter(
    "caesar_audit_events_total",
    "Audit-log rows written, labelled by event_type.",
    ["event_type"],
)


CHAT_DURATION = Histogram(
    "caesar_chat_duration_seconds",
    "End-to-end /v1/chat latency from request to response.",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 60.0),
)


class CaesarCollector:
    """Sampled gauges read from the running ``FastAPI`` app's state."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def collect(self) -> Iterator[Metric]:
        state: Any = self._app.state

        registry = getattr(state, "registry", None)
        workers = len(registry.workers) if registry is not None else 0
        yield GaugeMetricFamily(
            "caesar_workers_registered",
            "Currently registered Legion workers.",
            value=workers,
        )

        sweeper = getattr(state, "sweeper", None)
        sweeper_running = 1 if sweeper is not None and sweeper.is_running else 0
        yield GaugeMetricFamily(
            "caesar_retention_sweeper_running",
            "Whether the retention sweep loop is active (1) or not (0).",
            value=sweeper_running,
        )

        indexer = getattr(state, "semantic_indexer", None)
        indexer_running = 1 if indexer is not None and indexer.is_running else 0
        yield GaugeMetricFamily(
            "caesar_semantic_indexer_running",
            "Whether the semantic indexer loop is active (1) or not (0).",
            value=indexer_running,
        )

        audit_bus = getattr(state, "audit_bus", None)
        subscriber_count = audit_bus.subscriber_count if audit_bus is not None else 0
        yield GaugeMetricFamily(
            "caesar_audit_bus_subscribers",
            "Live SSE subscribers on the audit bus (dashboard tabs).",
            value=subscriber_count,
        )


def register_app_collector(app: FastAPI) -> CaesarCollector:
    """Register a :class:`CaesarCollector` for ``app``.

    Returns the collector so the caller can ``REGISTRY.unregister`` it
    at shutdown if needed (mostly for tests that build many apps).
    """

    collector = CaesarCollector(app)
    REGISTRY.register(collector)
    return collector


def unregister_collector(collector: CaesarCollector) -> None:
    """Unregister a collector. Safe to call when not registered."""

    import contextlib

    with contextlib.suppress(KeyError):  # pragma: no cover - defensive
        REGISTRY.unregister(collector)
