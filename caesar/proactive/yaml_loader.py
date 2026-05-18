"""Load and validate ``schedules.yaml`` / ``triggers.yaml`` (ADR-0030, ADR-0031).

The YAML is intentionally flat: each entry mixes trigger-level fields
(``id``, ``enabled``, ``prompt``, ``max_runtime_seconds``,
``cooldown_seconds``) with source-level fields. The loader lifts
the source fields under an explicit ``source:`` block before
validation:

- ``cron`` / ``timezone`` lift under ``source: {kind: schedule}``
  (v1.5).
- ``event_type`` / ``entity_id`` / ``to`` / ``time_window`` lift
  under ``source: {kind: ha_event}`` (v1.6, ADR-0031).
- ``timezone`` lifts to whichever source variant is being built.
- Mixing schedule and HA-event keys in one entry is an error.

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


_SCHEDULE_FIELDS = ("cron",)
_HA_EVENT_FIELDS = ("event_type", "entity_id", "to", "time_window")


class SchedulesConfig(BaseModel):
    """Parsed shape of ``schedules.yaml``."""

    version: Annotated[int, Field(ge=1, le=1)] = 1
    schedules: list[Trigger] = Field(default_factory=list)

    @field_validator("schedules", mode="before")
    @classmethod
    def _normalise_entries(cls, value: Any) -> Any:
        """Lift flat source fields into ``source: {kind:..., ...}``.

        Disambiguator: a top-level ``cron`` key means schedule, a
        top-level ``event_type`` key means ha_event. Having both is
        an error. Neither + no explicit ``source:`` block is also an
        error (caught by Pydantic — the discriminator can't pick a
        variant).
        """

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
                entry["source"] = _build_source_from_flat(entry)
            normalised.append(entry)
        return normalised


def _build_source_from_flat(entry: dict[str, Any]) -> dict[str, Any]:
    """Promote flat source keys into an explicit ``source:`` block.

    Mutates ``entry`` by popping the keys that belong to the chosen
    source variant. Always honours a top-level ``timezone`` by passing
    it into the source.
    """

    has_schedule_field = any(key in entry for key in _SCHEDULE_FIELDS)
    has_ha_event_field = any(key in entry for key in _HA_EVENT_FIELDS)
    if has_schedule_field and has_ha_event_field:
        raise ValueError(
            f"trigger entry mixes schedule fields {_SCHEDULE_FIELDS!r} with HA-event "
            f"fields {_HA_EVENT_FIELDS!r}; pick one. (id={entry.get('id')!r})"
        )

    if has_schedule_field:
        kind = "schedule"
        fields: tuple[str, ...] = _SCHEDULE_FIELDS
    else:
        # Default to ha_event when only HA fields appear, OR when neither
        # set is present (rare; the Pydantic discriminator will then
        # complain about missing event_type, giving a clear error).
        kind = "ha_event"
        fields = _HA_EVENT_FIELDS

    source: dict[str, Any] = {"kind": kind}
    for key in fields:
        if key in entry:
            source[key] = entry.pop(key)
    if "timezone" in entry:
        source["timezone"] = entry.pop("timezone")
    return source


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
