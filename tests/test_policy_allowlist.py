from __future__ import annotations

from caesar.ha.models import ServiceCall
from caesar.policy.allowlist import AllowlistPolicy
from caesar.policy.yaml_loader import (
    AllowedServiceRule,
    RulesConfig,
    TargetConstraint,
)


def _rules(*entries: str | AllowedServiceRule) -> RulesConfig:
    """Build a RulesConfig from a mixed list of bare strings + rule objects.

    The ``allowed_services`` field has a ``mode="before"`` normaliser
    that accepts strings; the cast is purely to placate mypy's view of
    the declared list element type.
    """

    return RulesConfig.model_validate(
        {"version": 1, "allowed_services": list(entries)},
    )


# --- backward-compatible "bare string" rules ---------------------------------


def test_allows_listed_service() -> None:
    policy = AllowlistPolicy(_rules("light.turn_on"))
    decision = policy.evaluate(ServiceCall(domain="light", service="turn_on"))
    assert decision.allowed is True
    assert decision.rule == "light.turn_on"
    assert "allow-list" in decision.reason


def test_denies_unlisted_service() -> None:
    policy = AllowlistPolicy(_rules("light.turn_on"))
    decision = policy.evaluate(ServiceCall(domain="lock", service="unlock"))
    assert decision.allowed is False
    assert decision.rule is None
    assert "not on the allow-list" in decision.reason


def test_allowed_services_property_lists_distinct_service_ids() -> None:
    policy = AllowlistPolicy(_rules("light.turn_on", "switch.toggle"))
    assert policy.allowed_services == frozenset({"light.turn_on", "switch.toggle"})


def test_empty_allow_list_denies_everything() -> None:
    policy = AllowlistPolicy(_rules())
    decision = policy.evaluate(ServiceCall(domain="light", service="turn_on"))
    assert decision.allowed is False


def test_bare_string_rule_allows_any_target() -> None:
    """Bare strings are fully permissive; target.entity_id is unchecked."""

    policy = AllowlistPolicy(_rules("light.turn_on"))
    decision = policy.evaluate(
        ServiceCall(
            domain="light",
            service="turn_on",
            target={"entity_id": "light.anything"},
        )
    )
    assert decision.allowed is True


# --- SR-005: parameter-level (entity_id) constraints -------------------------


def _entity_rule(service: str, entity_ids: list[str]) -> AllowedServiceRule:
    return AllowedServiceRule(
        service=service,
        target=TargetConstraint(entity_id=entity_ids),
    )


def test_constrained_rule_allows_matching_single_entity() -> None:
    policy = AllowlistPolicy(_rules(_entity_rule("light.turn_on", ["light.kitchen"])))
    decision = policy.evaluate(
        ServiceCall(
            domain="light",
            service="turn_on",
            target={"entity_id": "light.kitchen"},
        )
    )
    assert decision.allowed is True
    assert decision.rule == "light.turn_on"
    assert "light.kitchen" in decision.reason


def test_constrained_rule_denies_non_matching_entity() -> None:
    policy = AllowlistPolicy(_rules(_entity_rule("light.turn_on", ["light.kitchen"])))
    decision = policy.evaluate(
        ServiceCall(
            domain="light",
            service="turn_on",
            target={"entity_id": "light.bedroom"},
        )
    )
    assert decision.allowed is False
    assert decision.rule is None
    assert "light.bedroom" in decision.reason


def test_constrained_rule_handles_entity_id_list() -> None:
    """HA accepts entity_id as a list; ALL must be in the permitted set."""

    policy = AllowlistPolicy(
        _rules(_entity_rule("light.turn_on", ["light.kitchen", "light.living_room"])),
    )
    # All entities in the rule's set → allowed.
    decision = policy.evaluate(
        ServiceCall(
            domain="light",
            service="turn_on",
            target={"entity_id": ["light.kitchen", "light.living_room"]},
        )
    )
    assert decision.allowed is True

    # One entity outside the rule's set → denied (defends against
    # prompt-injected "turn off every light" style attacks).
    decision = policy.evaluate(
        ServiceCall(
            domain="light",
            service="turn_on",
            target={"entity_id": ["light.kitchen", "light.bedroom"]},
        )
    )
    assert decision.allowed is False


def test_constrained_rule_requires_target() -> None:
    """A call with no target cannot match a constrained rule."""

    policy = AllowlistPolicy(_rules(_entity_rule("light.turn_on", ["light.kitchen"])))
    decision = policy.evaluate(ServiceCall(domain="light", service="turn_on"))
    assert decision.allowed is False
    assert "requires target.entity_id" in decision.reason


def test_multiple_rules_for_same_service_union() -> None:
    """Two constrained rules for the same service OR together."""

    policy = AllowlistPolicy(
        _rules(
            _entity_rule("light.turn_on", ["light.kitchen"]),
            _entity_rule("light.turn_on", ["light.bedroom"]),
        ),
    )
    # Each entity matches one of the rules.
    for entity in ("light.kitchen", "light.bedroom"):
        decision = policy.evaluate(
            ServiceCall(
                domain="light",
                service="turn_on",
                target={"entity_id": entity},
            )
        )
        assert decision.allowed is True, f"{entity} should be allowed"

    # An entity in neither rule.
    decision = policy.evaluate(
        ServiceCall(
            domain="light",
            service="turn_on",
            target={"entity_id": "light.attic"},
        )
    )
    assert decision.allowed is False


def test_permissive_rule_short_circuits_constrained_rule() -> None:
    """If a bare-string rule exists for the service, the call is allowed.

    Operator chose to keep the rule permissive even after adding a more
    specific entry; the permissive one wins.
    """

    policy = AllowlistPolicy(
        _rules(
            "light.turn_on",  # bare string
            _entity_rule("light.turn_on", ["light.kitchen"]),
        ),
    )
    decision = policy.evaluate(
        ServiceCall(
            domain="light",
            service="turn_on",
            target={"entity_id": "light.anywhere"},
        )
    )
    assert decision.allowed is True
