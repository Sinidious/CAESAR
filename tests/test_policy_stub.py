from __future__ import annotations

from caesar.ha.models import ServiceCall
from caesar.policy.engine import DenyAllPolicy


def test_deny_all_denies_everything() -> None:
    policy = DenyAllPolicy()
    decision = policy.evaluate(ServiceCall(domain="light", service="turn_on"))
    assert decision.allowed is False
    assert "denied by default" in decision.reason


def test_deny_all_reason_names_the_call() -> None:
    policy = DenyAllPolicy()
    decision = policy.evaluate(ServiceCall(domain="switch", service="toggle"))
    assert "switch.toggle" in decision.reason
