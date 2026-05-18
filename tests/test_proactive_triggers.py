"""Tests for the proactive trigger Pydantic models (ADR-0030)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from caesar.proactive.triggers import (
    HASource,
    ScheduleSource,
    Trigger,
    matches_ha_event,
)


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


# --- cooldown_seconds (ADR-0031) -----------------------------------------


def test_trigger_cooldown_defaults_to_none() -> None:
    assert _valid_trigger().cooldown_seconds is None


@pytest.mark.parametrize("good", [1, 60, 86400])
def test_trigger_accepts_valid_cooldown(good: int) -> None:
    assert _valid_trigger(cooldown_seconds=good).cooldown_seconds == good


@pytest.mark.parametrize("bad", [0, -1, 86401])
def test_trigger_rejects_cooldown_out_of_range(bad: int) -> None:
    with pytest.raises(ValidationError):
        _valid_trigger(cooldown_seconds=bad)


# --- HASource validation (ADR-0031) --------------------------------------


def _valid_ha_source(**overrides: object) -> HASource:
    return HASource(event_type="state_changed", **overrides)  # type: ignore[arg-type]


def test_ha_source_defaults_to_state_changed_utc() -> None:
    src = HASource()
    assert src.kind == "ha_event"
    assert src.event_type == "state_changed"
    assert src.timezone == "UTC"
    assert src.entity_id is None
    assert src.to is None
    assert src.time_window is None


def test_ha_source_accepts_optional_constraints() -> None:
    src = _valid_ha_source(
        entity_id="binary_sensor.office_motion",
        to="on",
        time_window="22:00-06:00",
        timezone="America/Los_Angeles",
    )
    assert src.entity_id == "binary_sensor.office_motion"
    assert src.to == "on"
    assert src.time_window == "22:00-06:00"


def test_ha_source_rejects_bad_event_type() -> None:
    with pytest.raises(ValidationError, match="event_type"):
        HASource(event_type="StateChanged")


def test_ha_source_rejects_bad_entity_id() -> None:
    with pytest.raises(ValidationError, match="entity_id"):
        _valid_ha_source(entity_id="binary_sensor")


def test_ha_source_rejects_bad_time_window() -> None:
    with pytest.raises(ValidationError, match="time_window"):
        _valid_ha_source(time_window="22:00 to 06:00")


def test_ha_source_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError, match="unknown timezone"):
        _valid_ha_source(timezone="Mars/Olympus")


def test_ha_source_rejects_entity_constraint_on_non_state_event() -> None:
    """entity_id / to only make sense on state_changed (ADR-0031)."""

    with pytest.raises(
        ValidationError,
        match="entity_id / to constraints require event_type='state_changed'",
    ):
        HASource(event_type="zwave_node_alive", entity_id="binary_sensor.x")


# --- matches_ha_event (ADR-0031 §3) --------------------------------------


def _state_event(
    *,
    entity_id: str = "binary_sensor.office_motion",
    new_state: str = "on",
) -> dict[str, object]:
    return {
        "event_type": "state_changed",
        "data": {
            "entity_id": entity_id,
            "new_state": {"state": new_state},
        },
    }


def test_matcher_event_type_must_match_exactly() -> None:
    src = HASource(event_type="state_changed")
    assert matches_ha_event(src, _state_event())
    assert not matches_ha_event(src, {"event_type": "zwave_node_alive"})


def test_matcher_state_changed_matches_when_entity_and_to_match() -> None:
    src = _valid_ha_source(entity_id="binary_sensor.office_motion", to="on")
    assert matches_ha_event(src, _state_event())


def test_matcher_state_changed_rejects_wrong_entity() -> None:
    src = _valid_ha_source(entity_id="binary_sensor.office_motion", to="on")
    assert not matches_ha_event(src, _state_event(entity_id="binary_sensor.kitchen_motion"))


def test_matcher_state_changed_rejects_wrong_state() -> None:
    src = _valid_ha_source(entity_id="binary_sensor.office_motion", to="on")
    assert not matches_ha_event(src, _state_event(new_state="off"))


def test_matcher_state_changed_ignores_missing_data_safely() -> None:
    """Malformed events (missing data) should not fire — they shouldn't crash either."""

    src = _valid_ha_source(entity_id="binary_sensor.x", to="on")
    assert not matches_ha_event(src, {"event_type": "state_changed"})
    assert not matches_ha_event(src, {"event_type": "state_changed", "data": None})


def test_matcher_state_changed_with_no_entity_constraint_matches_any_entity() -> None:
    src = HASource(event_type="state_changed", to="on")
    assert matches_ha_event(src, _state_event(entity_id="light.kitchen"))
    assert matches_ha_event(src, _state_event(entity_id="switch.coffee"))


def test_matcher_non_state_event_only_checks_event_type() -> None:
    src = HASource(event_type="zwave_node_alive")
    assert matches_ha_event(src, {"event_type": "zwave_node_alive", "data": {"node_id": 7}})


# --- time_window matching ------------------------------------------------


def test_matcher_inside_normal_window() -> None:
    src = _valid_ha_source(time_window="09:00-17:00", timezone="UTC")
    now = datetime(2026, 5, 17, 10, 30, tzinfo=UTC)
    assert matches_ha_event(src, _state_event(), now=now)


def test_matcher_outside_normal_window() -> None:
    src = _valid_ha_source(time_window="09:00-17:00", timezone="UTC")
    now = datetime(2026, 5, 17, 8, 59, tzinfo=UTC)
    assert not matches_ha_event(src, _state_event(), now=now)


def test_matcher_window_is_exclusive_on_end() -> None:
    """end minute is excluded; common automation gotcha."""

    src = _valid_ha_source(time_window="09:00-17:00", timezone="UTC")
    # 17:00 exact → out (end exclusive); 16:59 → in.
    assert not matches_ha_event(src, _state_event(), now=datetime(2026, 5, 17, 17, 0, tzinfo=UTC))
    assert matches_ha_event(src, _state_event(), now=datetime(2026, 5, 17, 16, 59, tzinfo=UTC))


def test_matcher_cross_midnight_window_late_evening() -> None:
    """22:00-06:00 should include 23:00."""

    src = _valid_ha_source(time_window="22:00-06:00", timezone="UTC")
    now = datetime(2026, 5, 17, 23, 0, tzinfo=UTC)
    assert matches_ha_event(src, _state_event(), now=now)


def test_matcher_cross_midnight_window_early_morning() -> None:
    """22:00-06:00 should include 02:00 of the next day."""

    src = _valid_ha_source(time_window="22:00-06:00", timezone="UTC")
    now = datetime(2026, 5, 18, 2, 0, tzinfo=UTC)
    assert matches_ha_event(src, _state_event(), now=now)


def test_matcher_cross_midnight_window_excludes_afternoon() -> None:
    """22:00-06:00 should exclude 14:00."""

    src = _valid_ha_source(time_window="22:00-06:00", timezone="UTC")
    now = datetime(2026, 5, 17, 14, 0, tzinfo=UTC)
    assert not matches_ha_event(src, _state_event(), now=now)


def test_matcher_time_window_uses_source_timezone() -> None:
    """A 22:00 PT window should match 05:00 UTC the next day."""

    src = _valid_ha_source(time_window="22:00-23:00", timezone="America/Los_Angeles")
    # 22:30 PT on 2026-05-17 == 05:30 UTC on 2026-05-18 (PDT = UTC-7).
    now = datetime(2026, 5, 18, 5, 30, tzinfo=UTC)
    assert matches_ha_event(src, _state_event(), now=now)


def test_matcher_default_now_uses_wall_clock() -> None:
    """When ``now`` is omitted, matcher reads wall clock through the source tz."""

    # A window spanning the full day matches no matter when the test
    # runs — proves the default-now path is exercised.
    src = _valid_ha_source(time_window="00:00-23:59", timezone="UTC")
    assert matches_ha_event(src, _state_event())


# --- WebhookSource validation (ADR-0032) ----------------------------------


def test_webhook_source_accepts_long_token() -> None:
    from pydantic import SecretStr

    from caesar.proactive.triggers import WebhookSource

    src = WebhookSource(bearer_token=SecretStr("a" * 48))
    assert src.kind == "webhook"
    assert src.bearer_token.get_secret_value() == "a" * 48


def test_webhook_source_rejects_short_token() -> None:
    from pydantic import SecretStr

    from caesar.proactive.triggers import WebhookSource

    with pytest.raises(ValidationError, match="bearer_token must be at least"):
        WebhookSource(bearer_token=SecretStr("a" * 16))


def test_webhook_trigger_round_trips_through_discriminated_union() -> None:
    """A Trigger with a WebhookSource round-trips through model_dump+validate."""

    from pydantic import SecretStr

    from caesar.proactive.triggers import WebhookSource

    t = Trigger(
        id="github_pr_opened",
        prompt="brief me",
        source=WebhookSource(bearer_token=SecretStr("w" * 48)),
    )
    assert t.source.kind == "webhook"
