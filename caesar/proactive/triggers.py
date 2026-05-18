"""Pydantic shapes for proactive triggers (ADR-0030, ADR-0031, ADR-0032).

- v1.5: :class:`ScheduleSource` (cron-driven).
- v1.6: :class:`HASource` (Home Assistant WS events), plus
  :func:`matches_ha_event`.
- v1.7: :class:`WebhookSource` (HTTP POST with per-trigger bearer auth).

The matcher is intentionally coarse (ADR-0031, carried into ADR-0032):
the trigger says "wake the brain when X happens"; the brain prompt
decides what to do. No JSON-path filters in YAML, no boolean
combinators — the LLM has full context and can reason about it.
"""

from __future__ import annotations

import re
from datetime import datetime, time
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

TRIGGER_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
HA_EVENT_TYPE_PATTERN = re.compile(r"^[a-z0-9_]+$")
HA_ENTITY_ID_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
TIME_WINDOW_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")

STATE_CHANGED = "state_changed"


class ScheduleSource(BaseModel):
    """Cron-driven trigger source (ADR-0030)."""

    kind: Literal["schedule"] = "schedule"
    cron: str
    timezone: str = "UTC"

    @field_validator("cron")
    @classmethod
    def _validate_cron(cls, value: str) -> str:
        if not croniter.is_valid(value):
            raise ValueError(f"invalid cron expression: {value!r}")
        return value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value!r}") from exc
        return value


class HASource(BaseModel):
    """Home Assistant event-driven trigger source (ADR-0031).

    Matches in this order:

    1. ``event_type`` exact match.
    2. For ``state_changed``: optional ``entity_id`` exact match;
       optional ``to`` exact match against ``data.new_state.state``.
    3. Optional ``time_window`` (``HH:MM-HH:MM`` in ``timezone``),
       inclusive on the start minute, exclusive on the end minute.
       Cross-midnight windows allowed.
    """

    kind: Literal["ha_event"] = "ha_event"
    event_type: str = STATE_CHANGED
    entity_id: str | None = None
    to: str | None = None
    time_window: str | None = None
    timezone: str = "UTC"

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, value: str) -> str:
        if not HA_EVENT_TYPE_PATTERN.fullmatch(value):
            raise ValueError(
                f"invalid event_type (expected snake_case identifier): {value!r}",
            )
        return value

    @field_validator("entity_id")
    @classmethod
    def _validate_entity_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not HA_ENTITY_ID_PATTERN.fullmatch(value):
            raise ValueError(
                f"invalid entity_id (expected 'domain.entity'): {value!r}",
            )
        return value

    @field_validator("time_window")
    @classmethod
    def _validate_time_window(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not TIME_WINDOW_PATTERN.fullmatch(value):
            raise ValueError(
                f"invalid time_window (expected 'HH:MM-HH:MM' 24h): {value!r}",
            )
        return value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _validate_constraints_only_on_state_changed(self) -> HASource:
        if self.event_type != STATE_CHANGED and (self.entity_id is not None or self.to is not None):
            raise ValueError(
                "entity_id / to constraints require event_type='state_changed'; "
                f"got event_type={self.event_type!r}",
            )
        return self


MIN_WEBHOOK_TOKEN_CHARS = 32


class WebhookSource(BaseModel):
    """HTTP-webhook-driven trigger source (ADR-0032).

    Operator POSTs JSON to ``/v1/hook/{trigger_id}`` with
    ``Authorization: Bearer <token>``. The token is stored here as a
    :class:`SecretStr` so it doesn't leak into ``__repr__`` /
    structlog dumps.
    """

    kind: Literal["webhook"] = "webhook"
    bearer_token: SecretStr

    @field_validator("bearer_token")
    @classmethod
    def _validate_bearer_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if len(token) < MIN_WEBHOOK_TOKEN_CHARS:
            raise ValueError(
                f"bearer_token must be at least {MIN_WEBHOOK_TOKEN_CHARS} characters; "
                f"`caesar init` generates fresh 48-char tokens.",
            )
        return value


# Discriminated union of all source variants.
TriggerSource = Annotated[
    ScheduleSource | HASource | WebhookSource,
    Field(discriminator="kind"),
]


class Trigger(BaseModel):
    """One proactive trigger as declared in ``triggers.yaml``.

    ``source`` discriminates the firing mechanism (cron schedule vs
    HA event vs HTTP webhook). ``cooldown_seconds`` applies to the
    trigger as a whole so every source type shares one suppression
    semantics.
    """

    id: str
    enabled: bool = True
    prompt: str = Field(min_length=1)
    # 5 minutes default; the brain run plus tool calls should fit. A
    # ceiling of one hour keeps a runaway prompt from chewing tokens
    # forever; operators that need more should reconsider the design.
    max_runtime_seconds: int = Field(default=300, ge=1, le=3600)
    # After firing, the trigger suppresses matching events for this many
    # seconds. ``None`` = fire every match (right default for one-shot
    # events like ``event.water_leak_detected``). Must be > 0 if set.
    cooldown_seconds: int | None = Field(default=None, ge=1, le=86400)
    source: TriggerSource

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not TRIGGER_ID_PATTERN.fullmatch(value):
            raise ValueError(
                f"invalid trigger id (expected snake_case identifier): {value!r}",
            )
        return value


# --- HA event matcher --------------------------------------------------------


def matches_ha_event(
    source: HASource,
    event: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """Return True iff ``event`` matches the matcher's constraints.

    ``now`` is used only for ``time_window`` evaluation. Defaults to
    ``datetime.now(<source.timezone>)`` so production callers usually
    omit it; tests pass a controlled value.

    ``event`` is the HA WebSocket event payload — typically
    ``{"event_type": ..., "data": {...}, "origin": ..., ...}``.
    """

    if event.get("event_type") != source.event_type:
        return False

    if source.event_type == STATE_CHANGED:
        data = event.get("data") or {}
        if source.entity_id is not None and data.get("entity_id") != source.entity_id:
            return False
        if source.to is not None:
            new_state = data.get("new_state") or {}
            if new_state.get("state") != source.to:
                return False

    return not (
        source.time_window is not None
        and not _is_in_window(source.time_window, source.timezone, now=now)
    )


def _parse_time_window(value: str) -> tuple[time, time]:
    """Parse ``"HH:MM-HH:MM"`` into a pair of :class:`datetime.time`."""

    start_str, end_str = value.split("-", 1)
    start_h, start_m = (int(x) for x in start_str.split(":"))
    end_h, end_m = (int(x) for x in end_str.split(":"))
    return time(start_h, start_m), time(end_h, end_m)


def _is_in_window(
    window: str,
    tz_name: str,
    *,
    now: datetime | None,
) -> bool:
    """True iff ``now`` (in ``tz_name``) is inside ``window``.

    The window is inclusive on the start minute, exclusive on the end
    minute. Cross-midnight windows (e.g. ``22:00-06:00``) are allowed
    and mean "from 22:00 today to 06:00 tomorrow".
    """

    tz = ZoneInfo(tz_name)
    when = now.astimezone(tz) if now is not None else datetime.now(tz)
    start, end = _parse_time_window(window)
    current = when.time().replace(second=0, microsecond=0)

    if start <= end:
        return start <= current < end
    # Cross-midnight: in-window if at/after start OR before end.
    return current >= start or current < end
