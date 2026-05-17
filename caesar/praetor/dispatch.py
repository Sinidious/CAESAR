"""Shared HA service-call dispatch (ADR-0007 + ADR-0012 + ADR-0013).

One function so that whether a call originates from ``/v1/devices/services``
(a programmatic client) or from the brain's tool-use loop (a
conversational client), it goes through the same policy decision, the
same HA path, and writes the same audit shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from caesar.db.audit import AuditLogger
from caesar.ha.client import HAClient
from caesar.ha.models import ServiceCall
from caesar.policy.engine import Policy, PolicyDecision


@dataclass(frozen=True)
class DispatchOutcome:
    """Result of dispatching one ``ServiceCall``."""

    decision: PolicyDecision
    audit_log_id: int


async def dispatch_service_call(
    call: ServiceCall,
    *,
    ha: HAClient,
    policy: Policy,
    audit: AuditLogger,
    decision_id: str | None = None,
) -> DispatchOutcome:
    """Policy → HA → audit, returning the verdict and the audit row id."""

    decision = policy.evaluate(call)
    payload: dict[str, object] = {
        "domain": call.domain,
        "service": call.service,
        "target": call.target,
        "data": call.data,
        "decision": decision.model_dump(),
    }
    if decision_id is not None:
        payload["decision_id"] = decision_id

    if not decision.allowed:
        audit_id = await audit.record("policy.denied", payload)
        return DispatchOutcome(decision=decision, audit_log_id=audit_id)

    await ha.call_service(call)
    audit_id = await audit.record("service.called", payload)
    return DispatchOutcome(decision=decision, audit_log_id=audit_id)
