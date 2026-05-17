"""Tests for the brain safety preamble (SR-004).

The preamble must be present in every LLM gateway call. Operators can
customise the prompt *below* the preamble via the dashboard, but they
cannot disable the safety section: that's the entire point of
compose_system_prompt being owned by the brain graph, not the
operator-controlled settings.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.llm.gateway import ChatMessage
from caesar.policy.allowlist import AllowlistPolicy
from caesar.policy.engine import Policy
from caesar.policy.yaml_loader import AllowedServiceRule, RulesConfig
from caesar.praetor.graph import build_brain_graph
from caesar.praetor.safety import BRAIN_SAFETY_PREAMBLE, compose_system_prompt
from tests.conftest import FakeGateway


def _permissive_policy() -> Policy:
    return AllowlistPolicy(
        RulesConfig(
            version=1,
            allowed_services=[AllowedServiceRule(service="light.turn_on")],
        ),
    )


# --- unit tests for compose_system_prompt ------------------------------------


def test_compose_with_operator_prompt_prepends_preamble() -> None:
    composed = compose_system_prompt("Be concise.")
    assert composed.startswith(BRAIN_SAFETY_PREAMBLE)
    assert "Be concise." in composed
    # Operator prompt comes after the preamble, with a separator.
    assert composed.endswith("Be concise.")


def test_compose_without_operator_prompt_returns_preamble_only() -> None:
    assert compose_system_prompt(None) == BRAIN_SAFETY_PREAMBLE
    assert compose_system_prompt("") == BRAIN_SAFETY_PREAMBLE


def test_preamble_warns_about_tool_result_content() -> None:
    """Verify the preamble carries the key safety messages.

    Locks in the structural invariants of the preamble so a careless
    edit can't accidentally drop a guard.
    """

    text = BRAIN_SAFETY_PREAMBLE.lower()
    assert "tool result" in text or "tool_result" in text
    assert "instruction" in text  # tells the LLM not to follow them
    assert "policy" in text  # explicit "do not bypass the policy"


# --- end-to-end: the brain graph injects the preamble ------------------------


@pytest.mark.asyncio
async def test_brain_injects_preamble_in_gateway_call(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    audit = AuditLogger(engine)
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=None,
        policy=_permissive_policy(),
        audit=audit,
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="hi")],
            "system": "Be tersely sarcastic.",
            "model": "test-model",
            "decision_id": uuid.uuid4().hex,
            "iteration": 0,
        }
    )
    assert len(fake_gateway.calls) == 1
    system = fake_gateway.calls[0]["system"]
    assert system.startswith(BRAIN_SAFETY_PREAMBLE)
    assert "Be tersely sarcastic." in system


@pytest.mark.asyncio
async def test_brain_injects_preamble_when_operator_prompt_is_none(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    """A bare brain call with no operator prompt still gets the preamble."""

    audit = AuditLogger(engine)
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=None,
        policy=_permissive_policy(),
        audit=audit,
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="hi")],
            "system": None,
            "model": "test-model",
            "decision_id": uuid.uuid4().hex,
            "iteration": 0,
        }
    )
    system = fake_gateway.calls[0]["system"]
    assert system == BRAIN_SAFETY_PREAMBLE
