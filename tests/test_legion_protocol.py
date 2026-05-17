from __future__ import annotations

import pytest
from pydantic import ValidationError

from caesar.legion.protocol import (
    REGISTRATION_SUBJECT,
    TaskDispatch,
    TaskResult,
    WorkerRegistration,
    dispatch_subject,
)


def test_dispatch_subject_format() -> None:
    assert dispatch_subject("noop") == "legion.noop.dispatch"
    assert REGISTRATION_SUBJECT == "legion.registry.register"


def test_worker_registration_requires_capabilities() -> None:
    with pytest.raises(ValidationError):
        WorkerRegistration(worker_id="x", capabilities=[], version="0.0.1")


def test_task_dispatch_defaults_to_empty_payload() -> None:
    t = TaskDispatch(task_id="t1", capability="test.noop")
    assert t.payload == {}


def test_task_result_roundtrip() -> None:
    r = TaskResult(task_id="t1", worker_id="noop", success=True, result={"x": 1})
    raw = r.model_dump_json()
    again = TaskResult.model_validate_json(raw)
    assert again == r
