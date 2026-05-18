"""Tests for the proactive system-prompt composition (ADR-0030, SR-004)."""

from __future__ import annotations

from caesar.praetor.safety import (
    BRAIN_SAFETY_PREAMBLE,
    PROACTIVE_PREAMBLE,
    compose_system_prompt,
)


def test_compose_without_proactive_or_operator() -> None:
    out = compose_system_prompt(None)
    assert out == BRAIN_SAFETY_PREAMBLE


def test_compose_with_operator_prompt_only() -> None:
    out = compose_system_prompt("You are CAESAR.")
    assert out.startswith(BRAIN_SAFETY_PREAMBLE)
    assert out.endswith("You are CAESAR.")
    # Proactive preamble is NOT included when proactive=False.
    assert PROACTIVE_PREAMBLE not in out


def test_compose_with_proactive_inserts_bias_before_operator() -> None:
    out = compose_system_prompt("Operator override.", proactive=True)
    assert BRAIN_SAFETY_PREAMBLE in out
    assert PROACTIVE_PREAMBLE in out
    # Order: safety, then proactive, then operator (so operator can
    # refine but not undo).
    safety_idx = out.index(BRAIN_SAFETY_PREAMBLE)
    proactive_idx = out.index(PROACTIVE_PREAMBLE)
    operator_idx = out.index("Operator override.")
    assert safety_idx < proactive_idx < operator_idx


def test_compose_proactive_without_operator() -> None:
    out = compose_system_prompt(None, proactive=True)
    assert BRAIN_SAFETY_PREAMBLE in out
    assert PROACTIVE_PREAMBLE in out


def test_proactive_bias_mentions_notify_and_no_ha() -> None:
    # Document the contract: the bias must steer the model away from
    # acting on the house without an explicit direction in the prompt.
    assert "notify" in PROACTIVE_PREAMBLE
    assert "Home Assistant" in PROACTIVE_PREAMBLE
