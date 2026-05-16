"""Allow-list policy backed by the YAML rules file (ADR-0013).

A service call is allowed iff ``"{domain}.{service}"`` appears in the
loaded rules' ``allowed_services``. Anything else is denied. The
matched service identifier is reported as the firing rule so operators
can trace decisions in the audit log.
"""

from __future__ import annotations

from caesar.ha.models import ServiceCall
from caesar.policy.engine import PolicyDecision
from caesar.policy.yaml_loader import RulesConfig


class AllowlistPolicy:
    """Policy that only allows services on a configured allow-list."""

    def __init__(self, rules: RulesConfig) -> None:
        self._rules = rules
        self._allowed: frozenset[str] = frozenset(rules.allowed_services)

    @property
    def allowed_services(self) -> frozenset[str]:
        return self._allowed

    def evaluate(self, call: ServiceCall) -> PolicyDecision:
        identifier = f"{call.domain}.{call.service}"
        if identifier in self._allowed:
            return PolicyDecision(
                allowed=True,
                reason=f"{identifier} is on the allow-list.",
                rule=identifier,
            )
        return PolicyDecision(
            allowed=False,
            reason=(
                f"{identifier} is not on the allow-list. "
                "Add it to CAESAR_POLICY__RULES_PATH to enable."
            ),
            rule=None,
        )
