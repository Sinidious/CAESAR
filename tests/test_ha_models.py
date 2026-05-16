from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from caesar.ha.models import EntityState, ServiceCall


def test_entity_state_parses_minimal_payload() -> None:
    state = EntityState.model_validate({"entity_id": "light.kitchen", "state": "off"})
    assert state.entity_id == "light.kitchen"
    assert state.state == "off"
    assert state.attributes == {}
    assert state.last_changed is None


def test_entity_state_parses_full_payload() -> None:
    state = EntityState.model_validate(
        {
            "entity_id": "light.kitchen",
            "state": "on",
            "attributes": {"brightness": 200},
            "last_changed": "2026-05-16T12:00:00+00:00",
            "last_updated": "2026-05-16T12:00:00+00:00",
            "context": {"id": "ignored"},
        }
    )
    assert state.attributes == {"brightness": 200}
    assert state.last_changed == datetime.fromisoformat("2026-05-16T12:00:00+00:00")


def test_service_call_requires_non_empty_strings() -> None:
    with pytest.raises(ValidationError):
        ServiceCall(domain="", service="turn_on")
    with pytest.raises(ValidationError):
        ServiceCall(domain="light", service="")
