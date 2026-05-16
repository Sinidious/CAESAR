"""Policy types and the stub implementation (ADR-0013).

``Policy`` is a Protocol so tests inject a permissive policy without
touching real YAML. ``DenyAllPolicy`` is the safe default: until a
rules file is loaded, every service call is rejected.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from caesar.ha.models import ServiceCall


class PolicyDecision(BaseModel):
    """Result of evaluating a service call against the active policy.

    ``rule`` is the name of the rule that fired, if any. The allow-list
    policy uses the matched service identifier as the rule name; richer
    policies (named rules, conditions) populate it with a real name.
    """

    allowed: bool
    reason: str
    rule: str | None = None


class Policy(Protocol):
    """Authorisation contract for HA-bound side effects."""

    def evaluate(self, call: ServiceCall) -> PolicyDecision:
        """Return whether ``call`` may proceed and why."""
        ...


class DenyAllPolicy:
    """Stub policy: refuse everything.

    Loaded when no rules file is configured. PR B replaces this with a
    real allow-list policy backed by ``CAESAR_POLICY__RULES_PATH``.
    """

    def evaluate(self, call: ServiceCall) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            reason=(
                f"No policy rules loaded; {call.domain}.{call.service} denied"
                " by default. Set CAESAR_POLICY__RULES_PATH to enable."
            ),
        )
