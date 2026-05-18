"""Proactive triggers (ADR-0030).

Scheduler subsystem that fires declarative triggers into the brain
graph without an operator-facing request kicking them off. v1.5 ships
the scheduled (cron-like) source; HA-event and webhook sources slot
in cleanly in v1.6+.

Public surface:

- :class:`Trigger` and :class:`ScheduleSource` — the Pydantic shape
  declared in ``schedules.yaml``.
- :class:`Scheduler` — the asyncio task that walks the trigger list,
  sleeps until the next due fire, and invokes the injected callback.
- :func:`load_schedules` — read and validate ``schedules.yaml``.
"""

from __future__ import annotations

from caesar.proactive.scheduler import Scheduler, TriggerCallback
from caesar.proactive.triggers import ScheduleSource, Trigger
from caesar.proactive.yaml_loader import (
    SchedulesConfig,
    SchedulesError,
    load_schedules,
)

__all__ = [
    "ScheduleSource",
    "Scheduler",
    "SchedulesConfig",
    "SchedulesError",
    "Trigger",
    "TriggerCallback",
    "load_schedules",
]
