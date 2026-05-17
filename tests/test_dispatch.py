from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.db.schema import audit_log
from caesar.ha.client import HAClient
from caesar.ha.models import ServiceCall
from caesar.policy.allowlist import AllowlistPolicy
from caesar.policy.engine import DenyAllPolicy
from caesar.policy.yaml_loader import AllowedServiceRule, RulesConfig
from caesar.praetor.dispatch import dispatch_service_call


async def test_allow_path_calls_ha_and_audits(
    engine: AsyncEngine, mock_ha: HAClient, ha_service_calls: list[dict[str, Any]]
) -> None:
    audit = AuditLogger(engine)
    policy = AllowlistPolicy(
        RulesConfig(
            version=1,
            allowed_services=[AllowedServiceRule(service="light.turn_on")],
        )
    )
    outcome = await dispatch_service_call(
        ServiceCall(domain="light", service="turn_on"),
        ha=mock_ha,
        policy=policy,
        audit=audit,
        decision_id="dec-1",
    )
    assert outcome.decision.allowed is True
    assert outcome.audit_log_id >= 1
    assert ha_service_calls == [{"domain": "light", "service": "turn_on", "body": {}}]
    async with engine.connect() as conn:
        rows = (await conn.execute(select(audit_log))).all()
    assert rows[-1].event_type == "service.called"
    assert rows[-1].payload["decision_id"] == "dec-1"


async def test_deny_path_skips_ha_and_audits(
    engine: AsyncEngine, mock_ha: HAClient, ha_service_calls: list[dict[str, Any]]
) -> None:
    audit = AuditLogger(engine)
    outcome = await dispatch_service_call(
        ServiceCall(domain="lock", service="unlock"),
        ha=mock_ha,
        policy=DenyAllPolicy(),
        audit=audit,
    )
    assert outcome.decision.allowed is False
    assert ha_service_calls == []
    async with engine.connect() as conn:
        rows = (await conn.execute(select(audit_log))).all()
    assert rows[-1].event_type == "policy.denied"
    assert "decision_id" not in rows[-1].payload
