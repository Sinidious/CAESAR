"""Normalised HA types consumed by the rest of CAESAR.

HA's REST API is well-documented; we mirror the fields we actually
use and leave the rest as a permissive ``attributes`` dict. The point
of this module is that callers see typed objects, not raw dicts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EntityState(BaseModel):
    """Normalised view of one HA entity at a point in time."""

    model_config = ConfigDict(extra="ignore")

    entity_id: str
    state: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    last_changed: datetime | None = None
    last_updated: datetime | None = None


class ServiceCall(BaseModel):
    """Description of one HA service invocation."""

    domain: str = Field(min_length=1)
    service: str = Field(min_length=1)
    target: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
