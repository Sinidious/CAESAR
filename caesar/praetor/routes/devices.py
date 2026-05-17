"""``/v1/devices`` — read HA state, ask the policy, call HA services.

The route is the canonical write path into the home: every call goes
:class:`Policy` → :class:`HAClient`, via :func:`dispatch_service_call`
so the brain's tool-use loop shares the same path.
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status

from caesar.db.audit import AuditLogger
from caesar.ha.client import HAClient
from caesar.ha.models import EntityState, ServiceCall
from caesar.policy.engine import Policy
from caesar.praetor.dispatch import dispatch_service_call

router = APIRouter(tags=["devices"])


def _get_ha(request: Request) -> HAClient:
    ha = request.app.state.ha
    if ha is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "HA Bridge not configured (set CAESAR_HA__URL and CAESAR_HA__TOKEN).",
        )
    return cast(HAClient, ha)


def _get_policy(request: Request) -> Policy:
    return cast(Policy, request.app.state.policy)


def _get_audit(request: Request) -> AuditLogger:
    return cast(AuditLogger, request.app.state.audit)


@router.get("/v1/devices", response_model=list[EntityState])
async def list_devices(
    ha: Annotated[HAClient, Depends(_get_ha)],
) -> list[EntityState]:
    return await ha.list_states()


@router.get("/v1/devices/{entity_id}", response_model=EntityState)
async def get_device(
    entity_id: str,
    ha: Annotated[HAClient, Depends(_get_ha)],
) -> EntityState:
    state = await ha.get_state(entity_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown entity {entity_id!r}")
    return state


@router.post("/v1/devices/services", status_code=status.HTTP_200_OK)
async def call_service(
    call: ServiceCall,
    ha: Annotated[HAClient, Depends(_get_ha)],
    policy: Annotated[Policy, Depends(_get_policy)],
    audit: Annotated[AuditLogger, Depends(_get_audit)],
) -> dict[str, object]:
    outcome = await dispatch_service_call(call, ha=ha, policy=policy, audit=audit)
    if not outcome.decision.allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {
                "reason": outcome.decision.reason,
                "audit_log_id": outcome.audit_log_id,
            },
        )
    return {
        "domain": call.domain,
        "service": call.service,
        "target": call.target,
        "audit_log_id": outcome.audit_log_id,
    }
