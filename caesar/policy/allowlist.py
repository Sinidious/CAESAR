"""Allow-list policy backed by the YAML rules file (ADR-0013).

A service call is allowed iff some rule in ``allowed_services`` both
names ``"{domain}.{service}"`` *and* (when the rule constrains
parameters) covers the call's ``target.entity_id`` (SR-005).

Multiple rules for the same service are unioned: the first match
wins. The matched service identifier is reported as the firing rule
so operators can trace decisions in the audit log.
"""

from __future__ import annotations

from typing import Any

from caesar.ha.models import ServiceCall
from caesar.policy.engine import PolicyDecision
from caesar.policy.yaml_loader import AllowedServiceRule, RulesConfig


def _call_entity_ids(call: ServiceCall) -> list[str] | None:
    """Normalise the call's ``target.entity_id`` to a list, or ``None``."""

    if call.target is None:
        return None
    raw = call.target.get("entity_id")
    if raw is None:
        return None
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, str)]
    return None


class AllowlistPolicy:
    """Policy that only allows services on a configured allow-list."""

    def __init__(self, rules: RulesConfig) -> None:
        self._rules = rules
        # Pre-index rules by service identifier so evaluation is O(1) per
        # call regardless of the size of the rule set.
        self._by_service: dict[str, list[AllowedServiceRule]] = {}
        for rule in rules.allowed_services:
            self._by_service.setdefault(rule.service, []).append(rule)

    @property
    def allowed_services(self) -> frozenset[str]:
        """Set of service identifiers that have *some* allow rule.

        Tells you whether a service is on the list at all; a True
        membership doesn't imply any given call is allowed (a
        constrained rule may still deny based on ``entity_id``).
        """

        return frozenset(self._by_service.keys())

    def evaluate(self, call: ServiceCall) -> PolicyDecision:
        identifier = f"{call.domain}.{call.service}"
        candidates = self._by_service.get(identifier)
        if not candidates:
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"{identifier} is not on the allow-list. "
                    "Add it to CAESAR_POLICY__RULES_PATH to enable."
                ),
                rule=None,
            )

        call_entities = _call_entity_ids(call)
        constrained_rules: list[Any] = []
        for rule in candidates:
            if rule.is_permissive:
                return PolicyDecision(
                    allowed=True,
                    reason=f"{identifier} is on the allow-list.",
                    rule=identifier,
                )
            constrained_rules.append(rule)

        # All remaining rules constrain target.entity_id. Allow if the
        # call's entity IDs (a) exist and (b) are a subset of some rule's
        # permitted set.
        if call_entities is None:
            return PolicyDecision(
                allowed=False,
                reason=(f"{identifier} requires target.entity_id; call did not provide one."),
                rule=None,
            )

        for rule in constrained_rules:
            permitted = set(rule.target.entity_id or [])
            if all(e in permitted for e in call_entities):
                return PolicyDecision(
                    allowed=True,
                    reason=(
                        f"{identifier} is on the allow-list and target.entity_id "
                        f"{call_entities!r} matches the rule's permitted set."
                    ),
                    rule=identifier,
                )

        return PolicyDecision(
            allowed=False,
            reason=(
                f"{identifier} denied: target.entity_id {call_entities!r} is not "
                "covered by any allow-list rule for this service."
            ),
            rule=None,
        )
