from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.config import CaesarSettings
from caesar.db.schema import audit_log
from caesar.ha.client import HAClient
from caesar.ha.models import ServiceCall
from caesar.policy.engine import DenyAllPolicy, Policy, PolicyDecision


async def test_get_devices_when_ha_unconfigured_is_503(client: AsyncClient) -> None:
    r = await client.get("/v1/devices")
    assert r.status_code == 503


async def test_get_devices_lists_states(client_with_ha: AsyncClient) -> None:
    r = await client_with_ha.get("/v1/devices")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["entity_id"] == "light.kitchen"


async def test_get_one_device_hit(client_with_ha: AsyncClient) -> None:
    r = await client_with_ha.get("/v1/devices/light.kitchen")
    assert r.status_code == 200
    assert r.json()["entity_id"] == "light.kitchen"


async def test_get_one_device_miss(client_with_ha: AsyncClient) -> None:
    r = await client_with_ha.get("/v1/devices/light.bedroom")
    assert r.status_code == 404


async def test_call_service_with_allow_policy(
    client_with_ha: AsyncClient,
    ha_service_calls: list[dict[str, Any]],
    engine: AsyncEngine,
) -> None:
    r = await client_with_ha.post(
        "/v1/devices/services",
        json={
            "domain": "light",
            "service": "turn_on",
            "target": {"entity_id": "light.kitchen"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["domain"] == "light"
    assert body["service"] == "turn_on"
    assert body["audit_log_id"] >= 1

    # HA was actually called.
    assert ha_service_calls == [
        {
            "domain": "light",
            "service": "turn_on",
            "body": {"target": {"entity_id": "light.kitchen"}},
        }
    ]

    # Audit row is labelled service.called.
    async with engine.connect() as conn:
        rows = (await conn.execute(select(audit_log))).all()
    assert any(row.event_type == "service.called" for row in rows)


@pytest.fixture
async def client_with_deny(
    settings: CaesarSettings,
    engine: AsyncEngine,
    fake_gateway,
    mock_ha: HAClient,
) -> AsyncIterator[AsyncClient]:
    """HA configured, but the policy is the deny-all stub."""

    from caesar.praetor.app import create_app

    app = create_app(
        settings=settings,
        gateway=fake_gateway,
        engine=engine,
        ha=mock_ha,
        policy=DenyAllPolicy(),
    )
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


async def test_call_service_denied_returns_403_and_audits(
    client_with_deny: AsyncClient,
    ha_service_calls: list[dict[str, Any]],
    engine: AsyncEngine,
) -> None:
    r = await client_with_deny.post(
        "/v1/devices/services",
        json={"domain": "light", "service": "turn_on"},
    )
    assert r.status_code == 403
    # HA was NOT called.
    assert ha_service_calls == []
    # Audit row labelled policy.denied exists.
    async with engine.connect() as conn:
        rows = (await conn.execute(select(audit_log))).all()
    assert any(row.event_type == "policy.denied" for row in rows)


class _NoopGateway:
    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not called")


def test_default_policy_is_deny_all_stub(settings: CaesarSettings, engine: AsyncEngine) -> None:
    """Without an injected policy, app.state.policy is DenyAllPolicy."""

    from caesar.praetor.app import create_app

    app = create_app(settings=settings, gateway=_NoopGateway(), engine=engine)
    assert isinstance(app.state.policy, DenyAllPolicy)


class _OpenPolicy:
    """Mirror Policy implementations: same interface, different decision."""

    def evaluate(self, call: ServiceCall) -> PolicyDecision:
        return PolicyDecision(allowed=True, reason="open")


def test_policy_protocol_structural_check() -> None:
    """A duck-typed Policy can be used wherever the Protocol is expected."""

    p: Policy = _OpenPolicy()
    assert p.evaluate(ServiceCall(domain="x", service="y")).allowed
