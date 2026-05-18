"""Load and validate ``schedules.yaml`` (ADR-0030).

The YAML is intentionally flat: each schedule entry mixes the
trigger-level fields (``id``, ``enabled``, ``prompt``,
``max_runtime_seconds``) with the source-level fields (``cron``,
``timezone``). The loader lifts the source fields under
``source: {kind: schedule, ...}`` before validation, so when v1.6
adds HA-event and webhook sources the model can grow a discriminated
union without breaking existing files.

A nested ``source: {...}`` block is also accepted for operators who
want the explicit form.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from caesar.proactive.triggers import Trigger


class SchedulesError(RuntimeError):
    """Raised when the schedules file is missing or invalid."""


_SOURCE_FIELDS = ("cron", "timezone")


class SchedulesConfig(BaseModel):
    """Parsed shape of ``schedules.yaml``."""

    version: Annotated[int, Field(ge=1, le=1)] = 1
    schedules: list[Trigger] = Field(default_factory=list)

    @field_validator("schedules", mode="before")
    @classmethod
    def _normalise_entries(cls, value: Any) -> Any:
        """Lift bare ``cron``/``timezone`` keys into ``source: {kind:schedule,...}``."""

        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("schedules must be a list")
        normalised: list[Any] = []
        for entry in value:
            if not isinstance(entry, dict):
                normalised.append(entry)
                continue
            entry = dict(entry)
            if "source" not in entry:
                source: dict[str, Any] = {"kind": "schedule"}
                for key in _SOURCE_FIELDS:
                    if key in entry:
                        source[key] = entry.pop(key)
                entry["source"] = source
            normalised.append(entry)
        return normalised


def load_schedules(path: Path) -> SchedulesConfig:
    """Read and validate a ``schedules.yaml`` file.

    Raises :class:`SchedulesError` if the file is missing, isn't a
    mapping at the top level, or fails schema validation.
    """

    if not path.is_file():
        raise SchedulesError(f"schedules file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SchedulesError(f"schedules YAML parse error in {path}: {exc}") from exc
    if raw is None:
        return SchedulesConfig()
    if not isinstance(raw, dict):
        raise SchedulesError(
            f"schedules root must be a mapping; got {type(raw).__name__} in {path}"
        )
    try:
        return SchedulesConfig.model_validate(raw)
    except ValidationError as exc:
        raise SchedulesError(f"schedules schema error in {path}: {exc}") from exc
