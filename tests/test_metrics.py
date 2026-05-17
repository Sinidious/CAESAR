"""Tests for the /metrics endpoint and audit-event counter."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.config import CaesarSettings, DatabaseSettings, LLMSettings, LogSettings
from caesar.db.audit import AuditLogger
from caesar.praetor.app import create_app


def _settings(db_url: str) -> CaesarSettings:
    return CaesarSettings(
        db=DatabaseSettings(url=db_url),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
    )


@pytest.fixture
async def metrics_app(db_url: str, engine: AsyncEngine, fake_gateway):
    return create_app(settings=_settings(db_url), gateway=fake_gateway, engine=engine)


@pytest.fixture
async def metrics_client(metrics_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=metrics_app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        metrics_app.router.lifespan_context(metrics_app),
    ):
        yield ac


async def test_metrics_endpoint_returns_prometheus_text(
    metrics_client: AsyncClient,
) -> None:
    r = await metrics_client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "# HELP" in r.text
    # Built-in metric definitions appear.
    assert "caesar_audit_events_total" in r.text
    assert "caesar_chat_duration_seconds" in r.text
    assert "caesar_workers_registered" in r.text
    assert "caesar_retention_sweeper_running" in r.text
    assert "caesar_semantic_indexer_running" in r.text
    assert "caesar_audit_bus_subscribers" in r.text


async def test_audit_events_counter_increments(
    metrics_client: AsyncClient, engine: AsyncEngine
) -> None:
    audit = AuditLogger(engine)
    await audit.record("test.metric.tick", {"k": 1})

    r = await metrics_client.get("/metrics")
    assert r.status_code == 200
    # The labelled sample appears in the output.
    assert 'caesar_audit_events_total{event_type="test.metric.tick"} 1.0' in r.text


async def test_chat_route_records_latency(
    metrics_client: AsyncClient,
) -> None:
    await metrics_client.post("/v1/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    r = await metrics_client.get("/metrics")
    assert r.status_code == 200
    # Histogram exposes a _count series; one observation = count 1.
    assert "caesar_chat_duration_seconds_count" in r.text
    assert "caesar_chat_duration_seconds_bucket" in r.text


async def test_workers_gauge_zero_when_bus_disabled(
    metrics_client: AsyncClient,
) -> None:
    r = await metrics_client.get("/metrics")
    # No bus → no registry → workers gauge reports 0.
    assert "caesar_workers_registered 0.0" in r.text


async def test_sweeper_gauge_reflects_lifespan(
    metrics_client: AsyncClient,
) -> None:
    """While the app is up, the sweeper is running and the gauge is 1."""

    r = await metrics_client.get("/metrics")
    assert "caesar_retention_sweeper_running 1.0" in r.text
