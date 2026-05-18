"""Proactive triggers (ADR-0030, ADR-0031, ADR-0032).

Subsystem that fires declarative triggers into the brain graph without
an operator-facing request kicking them off.

- v1.5 shipped the scheduled (cron-like) source.
- v1.6 shipped the HA-event source.
- v1.7 ships the HTTP-webhook source.

The :data:`TriggerSource` discriminated union grows additively.

Public surface:

- :class:`Trigger`, :class:`ScheduleSource`, :class:`HASource`,
  :class:`WebhookSource`, :data:`TriggerSource` — the Pydantic shape
  declared in ``triggers.yaml`` (or, deprecated, ``schedules.yaml``).
- :func:`matches_ha_event` — the v1.6 HA matcher.
- :class:`Scheduler` — runs schedule-source triggers via cron.
- :class:`HAEventDriver` — runs HA-event-source triggers via the
  shared :class:`ResilientHAEventStream` subscription.
- :class:`WebhookDispatcher` — runs webhook-source triggers fed by
  the :func:`POST /v1/hook/{trigger_id}` FastAPI route.
- :func:`load_triggers` — read and validate ``triggers.yaml`` /
  ``schedules.yaml``.
"""

from __future__ import annotations

from caesar.proactive.ha_driver import HAEventDriver
from caesar.proactive.scheduler import Scheduler, TriggerCallback
from caesar.proactive.triggers import (
    HASource,
    ScheduleSource,
    Trigger,
    TriggerSource,
    WebhookSource,
    matches_ha_event,
)
from caesar.proactive.webhook_dispatcher import WebhookDispatcher
from caesar.proactive.yaml_loader import (
    SchedulesConfig,
    SchedulesError,
    TriggersConfig,
    TriggersError,
    load_schedules,
    load_triggers,
)

__all__ = [
    "HAEventDriver",
    "HASource",
    "ScheduleSource",
    "Scheduler",
    "SchedulesConfig",
    "SchedulesError",
    "Trigger",
    "TriggerCallback",
    "TriggerSource",
    "TriggersConfig",
    "TriggersError",
    "WebhookDispatcher",
    "WebhookSource",
    "load_schedules",
    "load_triggers",
    "matches_ha_event",
]
