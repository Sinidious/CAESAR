"""Tests for the generalised Policy Engine (ADR-0028).

Covers the new ``GenericToolCall`` shape, the ``allowed_tools`` YAML
grammar, and end-to-end ``AllowlistPolicy.evaluate`` on the tool
path. Existing ``ServiceCall`` behaviour is locked down by
:mod:`tests.test_policy_allowlist` / :mod:`tests.test_policy_yaml`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from caesar.ha.models import ServiceCall
from caesar.policy.allowlist import AllowlistPolicy
from caesar.policy.engine import (
    DenyAllPolicy,
    GenericToolCall,
    ToolCall,
    tool_id,
)
from caesar.policy.yaml_loader import (
    AllowedServiceRule,
    AllowedToolRule,
    PolicyRulesError,
    RulesConfig,
    load_rules,
)

# --- tool_id() unifies both shapes -----------------------------------------


def test_tool_id_service_call_collapses_to_domain_dot_service() -> None:
    assert tool_id(ServiceCall(domain="light", service="turn_on")) == "light.turn_on"


def test_tool_id_generic_returns_tool_field() -> None:
    assert tool_id(GenericToolCall(tool="calculator", input={"q": "1+1"})) == "calculator"


# --- DenyAllPolicy generalises -----------------------------------------------


def test_deny_all_denies_generic_tool_call() -> None:
    policy = DenyAllPolicy()
    decision = policy.evaluate(GenericToolCall(tool="calculator"))
    assert decision.allowed is False
    assert "calculator" in decision.reason


def test_deny_all_denies_service_call_same_message_shape() -> None:
    policy = DenyAllPolicy()
    decision = policy.evaluate(ServiceCall(domain="light", service="turn_on"))
    assert decision.allowed is False
    assert "light.turn_on" in decision.reason


# --- AllowlistPolicy ServiceCall path remains correct -----------------------


def _rules(
    *,
    services: list[str | AllowedServiceRule] | None = None,
    tools: list[str | AllowedToolRule] | None = None,
) -> RulesConfig:
    return RulesConfig.model_validate(
        {
            "version": 1,
            "allowed_services": list(services) if services else [],
            "allowed_tools": list(tools) if tools else [],
        }
    )


def test_allowlist_still_denies_unlisted_service_call() -> None:
    policy = AllowlistPolicy(_rules(services=["light.turn_on"]))
    decision = policy.evaluate(ServiceCall(domain="lock", service="unlock"))
    assert decision.allowed is False


# --- GenericToolCall path ---------------------------------------------------


def test_bare_tool_entry_allows_any_input() -> None:
    policy = AllowlistPolicy(_rules(tools=["calculator"]))
    decision = policy.evaluate(GenericToolCall(tool="calculator", input={"q": "1+1"}))
    assert decision.allowed is True
    assert decision.rule == "calculator"
    assert "allow-list" in decision.reason


def test_unknown_tool_is_denied() -> None:
    policy = AllowlistPolicy(_rules(tools=["calculator"]))
    decision = policy.evaluate(GenericToolCall(tool="web_search", input={"query": "x"}))
    assert decision.allowed is False
    assert decision.rule is None
    assert "web_search" in decision.reason


def test_allowed_tools_property_reflects_configured_ids() -> None:
    policy = AllowlistPolicy(_rules(tools=["calculator", "web_search"]))
    assert policy.allowed_tools == frozenset({"calculator", "web_search"})


def test_empty_tool_list_denies_every_tool_call() -> None:
    policy = AllowlistPolicy(_rules(services=["light.turn_on"]))  # no allowed_tools
    decision = policy.evaluate(GenericToolCall(tool="anything", input={}))
    assert decision.allowed is False


# --- per-tool input constraints (domain_allowlist) --------------------------


def test_domain_allowlist_passes_when_domain_in_list() -> None:
    rule = AllowedToolRule(
        tool="web_search",
        input={"domain_allowlist": ["wikipedia.org", "example.com"]},
    )
    policy = AllowlistPolicy(_rules(tools=[rule]))
    decision = policy.evaluate(
        GenericToolCall(tool="web_search", input={"query": "x", "domain": "wikipedia.org"})
    )
    assert decision.allowed is True


def test_domain_allowlist_denies_when_domain_not_in_list() -> None:
    rule = AllowedToolRule(
        tool="web_search",
        input={"domain_allowlist": ["wikipedia.org"]},
    )
    policy = AllowlistPolicy(_rules(tools=[rule]))
    decision = policy.evaluate(
        GenericToolCall(tool="web_search", input={"query": "x", "domain": "evil.example"})
    )
    assert decision.allowed is False
    assert decision.rule is None


def test_domain_allowlist_passes_when_call_has_no_domain_field() -> None:
    """The constraint is irrelevant if the call doesn't reference a domain."""

    rule = AllowedToolRule(
        tool="web_search",
        input={"domain_allowlist": ["wikipedia.org"]},
    )
    policy = AllowlistPolicy(_rules(tools=[rule]))
    decision = policy.evaluate(GenericToolCall(tool="web_search", input={"query": "x"}))
    assert decision.allowed is True


def test_multiple_tool_rules_union() -> None:
    """Two rules for the same tool with different constraints OR together."""

    policy = AllowlistPolicy(
        _rules(
            tools=[
                AllowedToolRule(
                    tool="web_search",
                    input={"domain_allowlist": ["wikipedia.org"]},
                ),
                AllowedToolRule(
                    tool="web_search",
                    input={"domain_allowlist": ["example.com"]},
                ),
            ]
        )
    )
    for domain in ("wikipedia.org", "example.com"):
        decision = policy.evaluate(
            GenericToolCall(tool="web_search", input={"query": "x", "domain": domain})
        )
        assert decision.allowed is True, domain
    decision = policy.evaluate(
        GenericToolCall(tool="web_search", input={"query": "x", "domain": "evil.example"})
    )
    assert decision.allowed is False


# --- YAML loader for the new allowed_tools block ----------------------------


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "rules.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def test_load_rules_parses_bare_string_tool_entries(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: 1\nallowed_tools:\n  - calculator\n  - web_search\n",
    )
    cfg = load_rules(p)
    assert [t.tool for t in cfg.allowed_tools] == ["calculator", "web_search"]
    assert all(t.input == {} for t in cfg.allowed_tools)


def test_load_rules_parses_object_tool_entries(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
version: 1
allowed_tools:
  - tool: calculator
  - tool: web_search
    input:
      domain_allowlist:
        - wikipedia.org
""",
    )
    cfg = load_rules(p)
    assert cfg.allowed_tools[0].tool == "calculator"
    assert cfg.allowed_tools[0].input == {}
    assert cfg.allowed_tools[1].tool == "web_search"
    assert cfg.allowed_tools[1].input == {"domain_allowlist": ["wikipedia.org"]}


def test_load_rules_rejects_malformed_tool_name(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: 1\nallowed_tools:\n  - WebSearch\n",
    )
    with pytest.raises(PolicyRulesError, match="invalid tool name"):
        load_rules(p)


def test_load_rules_rejects_non_list_tools(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: 1\nallowed_tools:\n  not-a-list: true\n",
    )
    with pytest.raises(PolicyRulesError, match="allowed_tools must be a list"):
        load_rules(p)


def test_load_rules_accepts_both_blocks_together(tmp_path: Path) -> None:
    """A real-world v1.3 policy file has both service and tool rules."""

    p = _write(
        tmp_path,
        """\
version: 1
allowed_services:
  - light.turn_on
allowed_tools:
  - calculator
""",
    )
    cfg = load_rules(p)
    assert [r.service for r in cfg.allowed_services] == ["light.turn_on"]
    assert [r.tool for r in cfg.allowed_tools] == ["calculator"]


def test_load_rules_default_allowed_tools_is_empty(tmp_path: Path) -> None:
    """Backward compat: pre-v1.3 YAMLs (no allowed_tools block) work."""

    p = _write(tmp_path, "version: 1\nallowed_services: [light.turn_on]\n")
    cfg = load_rules(p)
    assert cfg.allowed_tools == []


# --- Policy protocol still satisfied by injected stubs ----------------------


class _PermissivePolicy:
    """A policy that allows everything; useful as a Protocol witness."""

    def evaluate(self, call: ToolCall) -> object:
        from caesar.policy.engine import PolicyDecision

        return PolicyDecision(allowed=True, reason="permissive test policy")


def test_protocol_accepts_generic_tool_call() -> None:
    """Type-check: a Policy-shaped class evaluates a GenericToolCall."""

    policy: object = _PermissivePolicy()
    # Structural typing — the call shouldn't raise.
    decision = policy.evaluate(GenericToolCall(tool="x"))  # type: ignore[attr-defined]
    assert decision.allowed is True
