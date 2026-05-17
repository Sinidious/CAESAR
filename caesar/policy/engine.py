"""Policy types and the stub implementation (ADR-0013, extended by ADR-0028).

``Policy`` is a Protocol so tests inject a permissive policy without
touching real YAML. ``DenyAllPolicy`` is the safe default: until a
rules file is loaded, every tool call is rejected.

v1.3 (ADR-0028) generalises the Policy contract from ``ServiceCall``
to ``ToolCall``: a discriminated union of :class:`ServiceCall`
(existing HA shape) and :class:`GenericToolCall` (everything else).
The ``call.tool_id`` property gives both shapes a uniform identifier
the matchers can switch on.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from caesar.ha.models import ServiceCall


class GenericToolCall(BaseModel):
    """Non-HA tool invocation (ADR-0028).

    The brain graph emits one of these for every tool that isn't
    ``call_service``: calculator queries, web searches, calendar
    reads, future workers. ``input`` is the LLM-supplied argument
    dict (already shape-validated by the tool's pydantic schema
    upstream); the Policy Engine matches per-tool rules against it.
    """

    tool: str = Field(min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)


ToolCall = ServiceCall | GenericToolCall


def tool_id(call: ToolCall) -> str:
    """Uniform identifier for matching against allow-list rules.

    HA service calls collapse ``domain.service`` into the legacy
    identifier the existing ``allowed_services`` config uses;
    other tools surface their ``tool`` field directly.
    """

    if isinstance(call, ServiceCall):
        return f"{call.domain}.{call.service}"
    return call.tool


class PolicyDecision(BaseModel):
    """Result of evaluating a tool call against the active policy.

    ``rule`` is the name of the rule that fired, if any. The allow-list
    policy uses the matched tool identifier as the rule name; richer
    policies (named rules, conditions) populate it with a real name.
    """

    allowed: bool
    reason: str
    rule: str | None = None


class Policy(Protocol):
    """Authorisation contract for tool invocations (ADR-0028).

    Pre-v1.3 implementations took :class:`ServiceCall` directly;
    ``ToolCall`` is a superset (the existing HA shape plus the new
    :class:`GenericToolCall`), so structurally-typed callers that
    only need HA gating keep working.
    """

    def evaluate(self, call: ToolCall) -> PolicyDecision:
        """Return whether ``call`` may proceed and why."""
        ...


class DenyAllPolicy:
    """Stub policy: refuse everything.

    Loaded when no rules file is configured. ``AllowlistPolicy``
    replaces this when ``CAESAR_POLICY__RULES_PATH`` is set.
    """

    def evaluate(self, call: ToolCall) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            reason=(
                f"No policy rules loaded; {tool_id(call)} denied"
                " by default. Set CAESAR_POLICY__RULES_PATH to enable."
            ),
        )
