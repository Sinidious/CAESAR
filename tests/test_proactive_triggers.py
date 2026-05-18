"""Tests for the proactive trigger Pydantic models (ADR-0030)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from caesar.proactive.triggers import ScheduleSource, Trigger


def _valid_source(**overrides: object) -> ScheduleSource:
    return ScheduleSource(cron="0 7 * * *", **overrides)  # type: ignore[arg-type]


def _valid_trigger(**overrides: object) -> Trigger:
    base = {
        "id": "morning_brief",
        "prompt": "good morning",
        "source": _valid_source(),
    }
    base.update(overrides)
    return Trigger(**base)  # type: ignore[arg-type]


def test_schedule_source_defaults_to_utc() -> None:
    src = _valid_source()
    assert src.kind == "schedule"
    assert src.timezone == "UTC"


def test_schedule_source_accepts_iana_timezone() -> None:
    src = _valid_source(timezone="America/Los_Angeles")
    assert src.timezone == "America/Los_Angeles"


def test_schedule_source_rejects_invalid_cron() -> None:
    with pytest.raises(ValidationError, match="invalid cron expression"):
        ScheduleSource(cron="not a cron")


def test_schedule_source_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError, match="unknown timezone"):
        ScheduleSource(cron="0 7 * * *", timezone="Mars/Olympus")


def test_trigger_round_trip() -> None:
    t = _valid_trigger()
    assert t.enabled is True
    assert t.max_runtime_seconds == 300


def test_trigger_rejects_uppercase_id() -> None:
    with pytest.raises(ValidationError, match="invalid trigger id"):
        _valid_trigger(id="MorningBrief")


def test_trigger_rejects_id_with_spaces() -> None:
    with pytest.raises(ValidationError, match="invalid trigger id"):
        _valid_trigger(id="morning brief")


def test_trigger_rejects_empty_prompt() -> None:
    with pytest.raises(ValidationError):
        _valid_trigger(prompt="")


@pytest.mark.parametrize("bad", [0, -1, 3601])
def test_trigger_rejects_runtime_out_of_range(bad: int) -> None:
    with pytest.raises(ValidationError):
        _valid_trigger(max_runtime_seconds=bad)
