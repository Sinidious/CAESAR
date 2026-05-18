"""Load and validate ``triggers.yaml`` / ``schedules.yaml`` (ADR-0030, ADR-0031).

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

The canonical file name is ``triggers.yaml`` (ADR-0031 §5).
``schedules.yaml`` continues to work as a **deprecated alias** for
one release; :func:`load_triggers` reads it with a one-line warning
and the v1.7 release drops the fallback. Within the file, the
canonical top-level key is ``triggers:`` but ``schedules:`` is
accepted as the same deprecated alias.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from caesar.log import get_logger
from caesar.proactive.triggers import Trigger

logger = get_logger("caesar.proactive.yaml_loader")


class TriggersError(RuntimeError):
    """Raised when the triggers file is missing or invalid."""


# v1.6 alias: SchedulesError remains for one release as a deprecated
# alias on the new TriggersError name (ADR-0031 §5).
SchedulesError = TriggersError


_SCHEDULE_FIELDS = ("cron",)
_HA_EVENT_FIELDS = ("event_type", "entity_id", "to", "time_window")


class TriggersConfig(BaseModel):
    """Parsed shape of ``triggers.yaml`` / ``schedules.yaml``."""

    version: Annotated[int, Field(ge=1, le=1)] = 1
    triggers: list[Trigger] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accept_deprecated_schedules_key(cls, value: Any) -> Any:
        """Accept ``schedules:`` as a deprecated alias for ``triggers:``.

        Per ADR-0031 §5: the file is renamed to ``triggers.yaml``, and
        the top-level YAML key follows. ``schedules:`` continues to
        work for one release with a warning at load time.
        """

        if not isinstance(value, dict):
            return value
        value = dict(value)
        if "triggers" in value and "schedules" in value:
            raise ValueError(
                "triggers file has both 'triggers:' and 'schedules:' top-level keys; "
                "remove the deprecated 'schedules:' alias.",
            )
        if "schedules" in value and "triggers" not in value:
            logger.warning(
                "triggers.deprecated_schedules_key",
                message=("Top-level 'schedules:' key is deprecated; rename to 'triggers:'."),
            )
            value["triggers"] = value.pop("schedules")
        return value

    @field_validator("triggers", mode="before")
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
            raise ValueError("triggers must be a list")
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

    @property
    def schedules(self) -> list[Trigger]:
        """Deprecated alias for :attr:`triggers` (kept for one release)."""

        return self.triggers


# v1.6 alias for the renamed config class (ADR-0031 §5).
SchedulesConfig = TriggersConfig


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


def load_triggers(path: Path) -> TriggersConfig:
    """Read and validate a ``triggers.yaml`` file.

    Raises :class:`TriggersError` if the file is missing, isn't a
    mapping at the top level, or fails schema validation.
    """

    if not path.is_file():
        raise TriggersError(f"triggers file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TriggersError(f"triggers YAML parse error in {path}: {exc}") from exc
    if raw is None:
        return TriggersConfig()
    if not isinstance(raw, dict):
        raise TriggersError(f"triggers root must be a mapping; got {type(raw).__name__} in {path}")
    try:
        return TriggersConfig.model_validate(raw)
    except ValidationError as exc:
        raise TriggersError(f"triggers schema error in {path}: {exc}") from exc


# v1.5 alias kept for one release (ADR-0031 §5).
load_schedules = load_triggers
