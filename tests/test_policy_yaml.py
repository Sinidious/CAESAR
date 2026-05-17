from __future__ import annotations

from pathlib import Path

import pytest

from caesar.policy.yaml_loader import PolicyRulesError, load_rules


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "rules.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# --- backward-compatible bare-string parsing ---------------------------------


def test_load_rules_parses_flat_list(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: 1\nallowed_services:\n  - light.turn_on\n  - switch.toggle\n",
    )
    cfg = load_rules(p)
    assert cfg.version == 1
    services = [rule.service for rule in cfg.allowed_services]
    assert services == ["light.turn_on", "switch.toggle"]
    # All bare-string rules are permissive.
    assert all(rule.is_permissive for rule in cfg.allowed_services)


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
    with pytest.raises(PolicyRulesError, match="invalid service identifier"):
        load_rules(p)


def test_repo_example_policy_is_valid() -> None:
    """The example file shipped in the repo must parse cleanly."""

    repo_root = Path(__file__).resolve().parent.parent
    example = repo_root / "examples" / "policy.yaml"
    cfg = load_rules(example)
    services = {rule.service for rule in cfg.allowed_services}
    assert "light.turn_on" in services
    assert all("." in rule.service for rule in cfg.allowed_services)


# --- SR-005: object-form entries with entity_id constraints ------------------


def test_load_rules_parses_object_form_with_entity_id(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
version: 1
allowed_services:
  - light.turn_on
  - service: light.turn_off
    target:
      entity_id: [light.kitchen, light.living_room]
""",
    )
    cfg = load_rules(p)
    assert len(cfg.allowed_services) == 2
    assert cfg.allowed_services[0].service == "light.turn_on"
    assert cfg.allowed_services[0].is_permissive is True

    constrained = cfg.allowed_services[1]
    assert constrained.service == "light.turn_off"
    assert constrained.is_permissive is False
    assert constrained.target is not None
    assert constrained.target.entity_id == ["light.kitchen", "light.living_room"]


def test_load_rules_object_form_without_target_is_permissive(tmp_path: Path) -> None:
    """An object entry with no target block is the same as a bare string."""

    p = _write(
        tmp_path,
        """\
version: 1
allowed_services:
  - service: switch.toggle
""",
    )
    cfg = load_rules(p)
    assert cfg.allowed_services[0].is_permissive is True


def test_load_rules_rejects_malformed_entity_id(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
version: 1
allowed_services:
  - service: light.turn_on
    target:
      entity_id: [LightKitchen, "no.dot.in_three"]
""",
    )
    with pytest.raises(PolicyRulesError, match="invalid entity_id"):
        load_rules(p)


def test_load_rules_rejects_unknown_entry_shape(tmp_path: Path) -> None:
    """A scalar that isn't a string or mapping is rejected."""

    p = _write(
        tmp_path,
        "version: 1\nallowed_services:\n  - 42\n",
    )
    with pytest.raises(PolicyRulesError, match="unsupported allow-list entry"):
        load_rules(p)
