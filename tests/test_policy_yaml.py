from __future__ import annotations

from pathlib import Path

import pytest

from caesar.policy.yaml_loader import PolicyRulesError, load_rules


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "rules.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def test_load_rules_parses_flat_list(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: 1\nallowed_services:\n  - light.turn_on\n  - switch.toggle\n",
    )
    cfg = load_rules(p)
    assert cfg.version == 1
    assert cfg.allowed_services == ["light.turn_on", "switch.toggle"]


def test_load_rules_accepts_empty_list(tmp_path: Path) -> None:
    p = _write(tmp_path, "version: 1\nallowed_services: []\n")
    cfg = load_rules(p)
    assert cfg.allowed_services == []


def test_load_rules_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PolicyRulesError, match="not found"):
        load_rules(tmp_path / "does-not-exist.yaml")


def test_load_rules_invalid_yaml_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "version: 1\nallowed_services: [\n")
    with pytest.raises(PolicyRulesError, match="YAML parse error"):
        load_rules(p)


def test_load_rules_non_mapping_root_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "- not-a-mapping\n")
    with pytest.raises(PolicyRulesError, match="must be a mapping"):
        load_rules(p)


def test_load_rules_schema_error_raises(tmp_path: Path) -> None:
    # version: 2 is out of the supported range.
    p = _write(tmp_path, "version: 2\nallowed_services: []\n")
    with pytest.raises(PolicyRulesError, match="schema error"):
        load_rules(p)


def test_load_rules_rejects_malformed_service_identifiers(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: 1\nallowed_services:\n  - LightTurnOn\n  - 'no.dot.in_three'\n",
    )
    with pytest.raises(PolicyRulesError, match="invalid service identifiers"):
        load_rules(p)


def test_repo_example_policy_is_valid() -> None:
    """The example file shipped in the repo must parse cleanly."""

    repo_root = Path(__file__).resolve().parent.parent
    example = repo_root / "examples" / "policy.yaml"
    cfg = load_rules(example)
    assert "light.turn_on" in cfg.allowed_services
    assert all("." in s for s in cfg.allowed_services)
