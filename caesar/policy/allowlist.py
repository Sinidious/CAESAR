"""Allow-list policy backed by the YAML rules file (ADR-0013, ADR-0028).

For ``ServiceCall`` calls (HA's existing shape), the policy is allowed
iff some rule in ``allowed_services`` both names ``"{domain}.{service}"``
and (when constrained) covers the call's ``target.entity_id`` (SR-005).

For non-HA tool calls (``GenericToolCall``, ADR-0028), the policy is
allowed iff some rule in ``allowed_tools`` names the tool id. Per-tool
input constraints are checked by tool-specific matchers in
:func:`_input_matches`; an unknown tool id surfaces as denied with the
same shape as the service path.

Multiple rules for the same id are unioned: the first match wins. The
matched identifier is reported as the firing rule so operators can
trace decisions in the audit log.
"""

from __future__ import annotations

from typing import Any

from caesar.ha.models import ServiceCall
from caesar.policy.engine import (
    GenericToolCall,
    PolicyDecision,
    ToolCall,
    tool_id,
)
from caesar.policy.yaml_loader import (
    AllowedServiceRule,
    AllowedToolRule,
    RulesConfig,
)


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


def _domain_allowlist_matches(rule_input: dict[str, Any], call_input: dict[str, Any]) -> bool:
    """``domain_allowlist`` matcher.

    Used by ``web_search`` and similar URL-returning tools (ADR-0028).
    When the rule specifies ``domain_allowlist``, the call's
    ``domain`` (if present) must be in the rule's list. When the call
    doesn't reference a domain, the rule is irrelevant and passes
    through.
    """

    allow = rule_input.get("domain_allowlist")
    if not allow:
        return True
    requested = call_input.get("domain")
    if requested is None:
        return True
    return requested in allow


def _input_matches(rule: AllowedToolRule, call: GenericToolCall) -> bool:
    """Per-tool constraint matcher.

    For v1.3 we ship one generic constraint key (``domain_allowlist``)
    used by the web-search worker. New tools register their own
    matcher by adding a branch here; the YAML passes constraint
    blobs through unchanged so a tool's matcher can interpret them.
    """

    if not rule.input:
        return True  # bare entry — fully permissive
    return _domain_allowlist_matches(rule.input, call.input)


class AllowlistPolicy:
    """Policy that only allows tool invocations on a configured allow-list."""

    def __init__(self, rules: RulesConfig) -> None:
        self._rules = rules
        self._services_by_id: dict[str, list[AllowedServiceRule]] = {}
        for rule in rules.allowed_services:
            self._services_by_id.setdefault(rule.service, []).append(rule)
        self._tools_by_id: dict[str, list[AllowedToolRule]] = {}
        for trule in rules.allowed_tools:
            self._tools_by_id.setdefault(trule.tool, []).append(trule)

    @property
    def allowed_services(self) -> frozenset[str]:
        """Set of service identifiers that have *some* allow rule."""

        return frozenset(self._services_by_id.keys())

    @property
    def allowed_tools(self) -> frozenset[str]:
        """Set of tool identifiers that have *some* allow rule (ADR-0028)."""

        return frozenset(self._tools_by_id.keys())

    def evaluate(self, call: ToolCall) -> PolicyDecision:
        if isinstance(call, ServiceCall):
            return self._evaluate_service(call)
        return self._evaluate_tool(call)

    # -- ServiceCall path (existing HA semantics) ---------------------------

    def _evaluate_service(self, call: ServiceCall) -> PolicyDecision:
        identifier = f"{call.domain}.{call.service}"
        candidates = self._services_by_id.get(identifier)
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
        constrained_rules: list[AllowedServiceRule] = []
        for rule in candidates:
            if rule.is_permissive:
                return PolicyDecision(
                    allowed=True,
                    reason=f"{identifier} is on the allow-list.",
                    rule=identifier,
                )
            constrained_rules.append(rule)

        if call_entities is None:
            return PolicyDecision(
                allowed=False,
                reason=(f"{identifier} requires target.entity_id; call did not provide one."),
                rule=None,
            )

        for rule in constrained_rules:
            assert rule.target is not None and rule.target.entity_id is not None
            permitted = set(rule.target.entity_id)
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

    # -- GenericToolCall path (ADR-0028) ------------------------------------

    def _evaluate_tool(self, call: GenericToolCall) -> PolicyDecision:
        identifier = tool_id(call)
        candidates = self._tools_by_id.get(identifier)
        if not candidates:
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"tool {identifier!r} is not on the allow-list. "
                    "Add it to CAESAR_POLICY__RULES_PATH (allowed_tools) to enable."
                ),
                rule=None,
            )

        for rule in candidates:
            if _input_matches(rule, call):
                return PolicyDecision(
                    allowed=True,
                    reason=f"tool {identifier!r} is on the allow-list.",
                    rule=identifier,
                )

        return PolicyDecision(
            allowed=False,
            reason=(
                f"tool {identifier!r} denied: input {call.input!r} does not "
                "satisfy any allow-list rule for this tool."
            ),
            rule=None,
        )
