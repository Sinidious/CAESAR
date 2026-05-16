from __future__ import annotations

from caesar.ha.models import ServiceCall
from caesar.policy.allowlist import AllowlistPolicy
from caesar.policy.yaml_loader import RulesConfig


def _rules(*services: str) -> RulesConfig:
    return RulesConfig(version=1, allowed_services=list(services))


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


def test_allowed_services_property_is_immutable() -> None:
    policy = AllowlistPolicy(_rules("light.turn_on", "switch.toggle"))
    assert policy.allowed_services == frozenset({"light.turn_on", "switch.toggle"})


def test_empty_allow_list_denies_everything() -> None:
    policy = AllowlistPolicy(_rules())
    decision = policy.evaluate(ServiceCall(domain="light", service="turn_on"))
    assert decision.allowed is False
