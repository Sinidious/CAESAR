"""Policy engine (ADR-0013).

Sits between agents and the Home Assistant Bridge. Every service call
must be authorised by a :class:`Policy` before reaching HA. PR A
ships a deny-by-default stub; PR B replaces it with a YAML-loaded
allow-list.
"""

from caesar.policy.allowlist import AllowlistPolicy
from caesar.policy.engine import DenyAllPolicy, Policy, PolicyDecision
from caesar.policy.yaml_loader import PolicyRulesError, RulesConfig, load_rules

__all__ = [
    "AllowlistPolicy",
    "DenyAllPolicy",
    "Policy",
    "PolicyDecision",
    "PolicyRulesError",
    "RulesConfig",
    "load_rules",
]
