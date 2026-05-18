"""Pydantic shapes for proactive triggers (ADR-0030)."""

from __future__ import annotations

import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, Field, field_validator

TRIGGER_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")


class ScheduleSource(BaseModel):
    """Cron-driven trigger source.

    ``kind`` is the discriminator for the future ``TriggerSource``
    union (HA-event and webhook sources land in v1.6+). For v1.5 the
    YAML loader hard-codes it to ``"schedule"``.
    """

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


class Trigger(BaseModel):
    """One proactive trigger as declared in ``schedules.yaml``."""

    id: str
    enabled: bool = True
    prompt: str = Field(min_length=1)
    # 5 minutes default; the brain run plus tool calls should fit. A
    # ceiling of one hour keeps a runaway prompt from chewing tokens
    # forever; operators that need more should reconsider the design.
    max_runtime_seconds: int = Field(default=300, ge=1, le=3600)
    source: ScheduleSource

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not TRIGGER_ID_PATTERN.fullmatch(value):
            raise ValueError(
                f"invalid trigger id (expected snake_case identifier): {value!r}",
            )
        return value
