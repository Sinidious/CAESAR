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
    from caesar.proactive.triggers import ScheduleSource

    assert isinstance(t.source, ScheduleSource)
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
    from caesar.proactive.triggers import ScheduleSource

    config = load_schedules(path)
    source = config.schedules[0].source
    assert isinstance(source, ScheduleSource)
    assert source.cron == "0 * * * *"


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


# --- HA-event flat form (ADR-0031) ---------------------------------------


def test_load_flat_ha_event_form(tmp_path: Path) -> None:
    """Top-level event_type/entity_id/to/time_window lift under
    source.kind=ha_event when no `cron` is present."""

    path = _write(
        tmp_path,
        """
        version: 1
        schedules:
          - id: late_office_motion
            enabled: true
            cooldown_seconds: 600
            prompt: "look into this"
            event_type: state_changed
            entity_id: binary_sensor.office_motion
            to: "on"
            time_window: "22:00-06:00"
            timezone: "America/Los_Angeles"
        """,
    )
    config = load_schedules(path)
    assert len(config.schedules) == 1
    t = config.schedules[0]
    assert t.cooldown_seconds == 600
    assert t.source.kind == "ha_event"
    assert t.source.event_type == "state_changed"
    assert t.source.entity_id == "binary_sensor.office_motion"
    assert t.source.to == "on"
    assert t.source.time_window == "22:00-06:00"
    assert t.source.timezone == "America/Los_Angeles"


def test_load_nested_ha_event_form(tmp_path: Path) -> None:
    """Explicit source: {kind: ha_event, ...} block is accepted unchanged."""

    path = _write(
        tmp_path,
        """
        schedules:
          - id: water_leak
            prompt: "ping me"
            source:
              kind: ha_event
              event_type: water_leak_detected
        """,
    )
    config = load_schedules(path)
    assert config.schedules[0].source.kind == "ha_event"
    assert config.schedules[0].source.event_type == "water_leak_detected"


def test_load_mixed_schedule_and_ha_event_in_one_file(tmp_path: Path) -> None:
    """Two entries with different source kinds load side-by-side."""

    path = _write(
        tmp_path,
        """
        schedules:
          - id: morning_brief
            cron: "0 7 * * *"
            prompt: brief me
          - id: motion_alert
            event_type: state_changed
            entity_id: binary_sensor.motion
            to: "on"
            prompt: alert me
        """,
    )
    config = load_schedules(path)
    kinds = [t.source.kind for t in config.schedules]
    assert kinds == ["schedule", "ha_event"]


def test_load_rejects_mixed_source_fields_in_one_entry(tmp_path: Path) -> None:
    """`cron` + `event_type` in one entry is ambiguous; reject loudly."""

    path = _write(
        tmp_path,
        """
        schedules:
          - id: ambiguous
            cron: "0 7 * * *"
            event_type: state_changed
            prompt: hi
        """,
    )
    with pytest.raises(SchedulesError, match="mixes schedule fields"):
        load_schedules(path)


def test_load_promotes_timezone_into_ha_source(tmp_path: Path) -> None:
    """Top-level timezone applies to whichever source variant is built."""

    path = _write(
        tmp_path,
        """
        schedules:
          - id: late_motion
            event_type: state_changed
            entity_id: binary_sensor.motion
            to: "on"
            time_window: "22:00-06:00"
            timezone: "America/New_York"
            prompt: hi
        """,
    )
    config = load_schedules(path)
    assert config.schedules[0].source.timezone == "America/New_York"


def test_load_schedules_none_treated_as_empty(tmp_path: Path) -> None:
    """An explicit ``schedules: null`` is normalised to an empty list."""

    path = _write(tmp_path, "schedules: null\n")
    config = load_schedules(path)
    assert config.schedules == []
