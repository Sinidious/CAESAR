"""HA event driver — the v1.6 counterpart to the v1.5 Scheduler.

The :class:`Scheduler` walks cron-driven triggers; the
:class:`HAEventDriver` walks HA-event-driven ones. Both share
:class:`caesar.proactive.runner.ProactiveRunner` for the actual
brain dispatch.

Lifecycle (ADR-0031 §2, §4, §6):

- ``start()`` emits one ``trigger.subscribed`` audit row per armed
  trigger, then opens a single shared
  :class:`ResilientHAEventStream` (one WS subscription per Praetor
  instance, demuxed in-process to per-trigger matchers).
- For every incoming event, each armed HASource trigger is checked
  against :func:`matches_ha_event`. On match, the per-trigger
  ``cooldown_seconds`` is enforced.
- Inside the cooldown window, the match is *suppressed* and
  coalesced — one ``trigger.suppressed`` audit row per cooldown
  window, carrying ``count`` and the timestamps of the first and
  last suppressed event. The row is flushed when the next allowed
  fire happens, or on ``stop()``.
- ``stop()`` cancels the stream and flushes any pending suppression
  rows so the audit log is complete.

The driver does not implement reconnect itself — the stream does
(ADR-0031 §2). It also does not store any state on disk: cooldown
counters are in-memory only, which means a restart resets every
cooldown. Operators who care about that gap should adopt a webhook
source (v1.7) where the brain decides on receipt.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from caesar.db.audit import AuditLogger
from caesar.ha.client import HAClient
from caesar.ha.subscription import ResilientHAEventStream
from caesar.log import get_logger
from caesar.proactive.runner import ProactiveRunner
from caesar.proactive.triggers import HASource, Trigger, matches_ha_event

logger = get_logger("caesar.proactive.ha_driver")


def _now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Suppression:
    """Coalescing state for cooldown-suppressed events on one trigger."""

    count: int
    first_event_at: datetime
    last_event_at: datetime


class HAEventDriver:
    """Drives HA-event triggers through the brain via ProactiveRunner."""

    def __init__(
        self,
        triggers: Iterable[Trigger],
        *,
        ha: HAClient,
        runner: ProactiveRunner,
        audit: AuditLogger,
        clock: Callable[[], datetime] = _now_utc,
        stream: ResilientHAEventStream | None = None,
    ) -> None:
        self._runner = runner
        self._audit = audit
        self._clock = clock
        self._armed: list[Trigger] = [
            t for t in triggers if t.enabled and isinstance(t.source, HASource)
        ]
        self._last_fired: dict[str, datetime] = {}
        self._suppressed: dict[str, _Suppression] = {}
        self._stream = stream or ResilientHAEventStream(
            ha,
            self._on_event,
            event_type=self._chosen_event_type(),
            audit=audit,
        )

    @property
    def armed_count(self) -> int:
        return len(self._armed)

    def _chosen_event_type(self) -> str | None:
        """Pick the WS subscription's event_type.

        When all armed triggers ask for the same event_type, subscribe
        to that one (cheaper). Otherwise subscribe to all events and
        let the per-trigger matcher filter in-process. Per ADR-0031 §2.
        """

        types: set[str] = set()
        for trigger in self._armed:
            assert isinstance(trigger.source, HASource)
            types.add(trigger.source.event_type)
        if len(types) == 1:
            return next(iter(types))
        return None

    async def announce(self) -> None:
        """Emit ``trigger.subscribed`` per armed trigger."""

        for trigger in self._armed:
            assert isinstance(trigger.source, HASource)
            await self._audit.record(
                "trigger.subscribed",
                {
                    "trigger_id": trigger.id,
                    "event_type": trigger.source.event_type,
                    "entity_id": trigger.source.entity_id,
                    "to": trigger.source.to,
                    "time_window": trigger.source.time_window,
                    "cooldown_seconds": trigger.cooldown_seconds,
                },
            )

    async def start(self) -> None:
        await self.announce()
        await self._stream.start()

    async def stop(self) -> None:
        await self._stream.stop()
        await self._flush_pending_suppressions()

    # --- event handling -------------------------------------------------

    async def _on_event(self, event: dict[str, Any]) -> None:
        now = self._clock()
        for trigger in self._armed:
            assert isinstance(trigger.source, HASource)
            if not matches_ha_event(trigger.source, event, now=now):
                continue
            if self._is_in_cooldown(trigger, now):
                self._record_suppression(trigger.id, now)
                logger.info(
                    "proactive.ha_event.suppressed",
                    trigger_id=trigger.id,
                    cooldown_seconds=trigger.cooldown_seconds,
                )
                continue
            await self._flush_suppression(trigger.id)
            self._last_fired[trigger.id] = now
            logger.info(
                "proactive.ha_event.match",
                trigger_id=trigger.id,
                event_type=trigger.source.event_type,
            )
            await self._runner.fire(trigger)

    def _is_in_cooldown(self, trigger: Trigger, now: datetime) -> bool:
        if trigger.cooldown_seconds is None:
            return False
        last = self._last_fired.get(trigger.id)
        if last is None:
            return False
        elapsed = (now - last).total_seconds()
        return elapsed < trigger.cooldown_seconds

    # --- suppression coalescing ----------------------------------------

    def _record_suppression(self, trigger_id: str, now: datetime) -> None:
        existing = self._suppressed.get(trigger_id)
        if existing is None:
            self._suppressed[trigger_id] = _Suppression(
                count=1, first_event_at=now, last_event_at=now
            )
            return
        existing.count += 1
        existing.last_event_at = now

    async def _flush_suppression(self, trigger_id: str) -> None:
        pending = self._suppressed.pop(trigger_id, None)
        if pending is None:
            return
        await self._audit.record(
            "trigger.suppressed",
            {
                "trigger_id": trigger_id,
                "count": pending.count,
                "first_event_at": pending.first_event_at.isoformat(),
                "last_event_at": pending.last_event_at.isoformat(),
            },
        )

    async def _flush_pending_suppressions(self) -> None:
        for trigger_id in list(self._suppressed):
            await self._flush_suppression(trigger_id)
