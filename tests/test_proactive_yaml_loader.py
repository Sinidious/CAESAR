"""Tests for the ``schedules.yaml`` loader (ADR-0030)."""

from __future__ import annotations

from pathlib import Path

import pytest

from caesar.proactive.yaml_loader import (
    SchedulesConfig,
    SchedulesError,
    load_schedules,
)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "schedules.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_flat_form(tmp_path: Path) -> None:
    """Top-level cron / timezone get lifted under source."""

    path = _write(
        tmp_path,
        """
        version: 1
        schedules:
          - id: morning_brief
            enabled: false
            cron: "0 7 * * 1-5"
            timezone: "America/Los_Angeles"
            max_runtime_seconds: 120
            prompt: "summarise calendar"
        """,
    )
    config = load_schedules(path)
    assert len(config.schedules) == 1
    t = config.schedules[0]
    assert t.id == "morning_brief"
    assert t.enabled is False
    assert t.prompt == "summarise calendar"
    assert t.source.kind == "schedule"
    assert t.source.cron == "0 7 * * 1-5"
    assert t.source.timezone == "America/Los_Angeles"


def test_load_nested_form(tmp_path: Path) -> None:
    """Explicit source: {kind: schedule, ...} block is accepted unchanged."""

    path = _write(
        tmp_path,
        """
        schedules:
          - id: hourly_ping
            prompt: still here
            source:
              kind: schedule
              cron: "0 * * * *"
              timezone: UTC
        """,
    )
    config = load_schedules(path)
    assert config.schedules[0].source.cron == "0 * * * *"


def test_load_empty_file_returns_empty_config(tmp_path: Path) -> None:
    path = _write(tmp_path, "")
    config = load_schedules(path)
    assert isinstance(config, SchedulesConfig)
    assert config.schedules == []


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SchedulesError, match="not found"):
        load_schedules(tmp_path / "missing.yaml")


def test_load_non_mapping_root_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "- not a mapping")
    with pytest.raises(SchedulesError, match="must be a mapping"):
        load_schedules(path)


def test_load_invalid_yaml_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "schedules: [")
    with pytest.raises(SchedulesError, match="YAML parse error"):
        load_schedules(path)


def test_load_schedules_not_a_list_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "schedules: nope")
    with pytest.raises(SchedulesError, match="schedules must be a list"):
        load_schedules(path)


def test_load_schedules_entry_not_a_mapping_is_passed_through(tmp_path: Path) -> None:
    """Non-dict entries propagate to the Pydantic validator, which rejects them."""

    path = _write(tmp_path, "schedules:\n  - just-a-string\n")
    with pytest.raises(SchedulesError, match="schema error"):
        load_schedules(path)


def test_load_schedules_invalid_cron_raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        schedules:
          - id: bad
            prompt: hi
            cron: "not a cron"
        """,
    )
    with pytest.raises(SchedulesError, match="schema error"):
        load_schedules(path)


def test_load_schedules_version_above_one_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: 2\nschedules: []\n")
    with pytest.raises(SchedulesError, match="schema error"):
        load_schedules(path)


def test_load_schedules_none_treated_as_empty(tmp_path: Path) -> None:
    """An explicit ``schedules: null`` is normalised to an empty list."""

    path = _write(tmp_path, "schedules: null\n")
    config = load_schedules(path)
    assert config.schedules == []
