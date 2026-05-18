"""Asyncio scheduler that fires proactive triggers (ADR-0030).

Single in-process task per Praetor instance. Walks the enabled trigger
list, sleeps until the next earliest fire, then invokes an injected
callback under ``asyncio.timeout(max_runtime_seconds)``.

The scheduler does not import the brain graph; it takes a
:class:`TriggerCallback` so this module can be unit-tested without
spinning up the LLM stack. The end-to-end wiring lives in the
Praetor lifespan (v1.5 follow-up).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from caesar.db.audit import AuditLogger
from caesar.log import get_logger
from caesar.proactive.triggers import Trigger

logger = get_logger("caesar.proactive.scheduler")

# Cap on the longest single asyncio.sleep the scheduler issues. Bounded
# so a future hot-reload of schedules.yaml can take effect within a
# minute even when the next fire is hours away.
_MAX_SLEEP_SECONDS = 60.0


# Coroutine signature the scheduler invokes when a trigger fires.
TriggerCallback = Callable[[Trigger], Awaitable[None]]


@dataclass
class _Armed:
    trigger: Trigger
    next_fire: datetime  # always UTC


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _next_fire(trigger: Trigger, *, after: datetime) -> datetime:
    """Compute the next fire time for ``trigger``, strictly after ``after``."""

    tz = ZoneInfo(trigger.source.timezone)
    base = after.astimezone(tz)
    itr = croniter(trigger.source.cron, base)
    nxt: datetime = itr.get_next(datetime)
    return nxt.astimezone(UTC)


class Scheduler:
    """Drives the enabled triggers forward through wall-clock time."""

    def __init__(
        self,
        triggers: Iterable[Trigger],
        callback: TriggerCallback,
        *,
        audit: AuditLogger | None = None,
        time_provider: Callable[[], datetime] = _now_utc,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._callback = callback
        self._audit = audit
        self._time_provider = time_provider
        self._sleep = sleep
        now = self._time_provider()
        self._armed: list[_Armed] = [
            _Armed(t, _next_fire(t, after=now)) for t in triggers if t.enabled
        ]
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def armed_count(self) -> int:
        return len(self._armed)

    def next_fire_for(self, trigger_id: str) -> datetime | None:
        for armed in self._armed:
            if armed.trigger.id == trigger_id:
                return armed.next_fire
        return None

    async def announce(self) -> None:
        """Audit-log ``trigger.scheduled`` for every armed trigger."""

        if self._audit is None:
            return
        for armed in self._armed:
            await self._audit.record(
                "trigger.scheduled",
                {
                    "trigger_id": armed.trigger.id,
                    "cron": armed.trigger.source.cron,
                    "timezone": armed.trigger.source.timezone,
                    "next_fire_at": armed.next_fire.isoformat(),
                },
            )

    async def tick(self) -> int:
        """Fire every armed trigger whose next_fire is at or before now.

        Returns the number of triggers fired. Tests use this to drive
        the scheduler forward without wall-clock sleeps.
        """

        fired = 0
        while self._armed:
            armed = min(self._armed, key=lambda a: a.next_fire)
            now = self._time_provider()
            if armed.next_fire > now:
                break
            await self._fire(armed)
            armed.next_fire = _next_fire(armed.trigger, after=self._time_provider())
            fired += 1
        return fired

    async def start(self) -> None:
        """Announce armed triggers and start the background task."""

        if self._task is not None:
            return
        await self.announce()
        self._task = asyncio.create_task(self._run(), name="caesar.proactive.scheduler")

    async def stop(self) -> None:
        """Signal the loop to exit and await the task."""

        self._stop.set()
        task = self._task
        if task is not None:
            self._task = None
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        # tick() never removes triggers, so self._armed is invariant once
        # the loop body runs at least once. If the scheduler was started
        # with no enabled triggers the while-condition short-circuits.
        while not self._stop.is_set() and self._armed:
            await self.tick()
            armed = min(self._armed, key=lambda a: a.next_fire)
            now = self._time_provider()
            delay = max(0.0, (armed.next_fire - now).total_seconds())
            delay = min(delay, _MAX_SLEEP_SECONDS)
            if delay > 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    return
                except TimeoutError:
                    continue
            else:  # pragma: no cover - tick() drains all due triggers
                # Defensive yield to the loop if a trigger fired and the
                # next fire is somehow already due (clock skew between
                # tick() and the delay computation).
                await self._sleep(0)

    async def _fire(self, armed: _Armed) -> None:
        trigger = armed.trigger
        started_at = self._time_provider()
        await self._record(
            "trigger.fired",
            {
                "trigger_id": trigger.id,
                "prompt": trigger.prompt,
                "scheduled_for": armed.next_fire.isoformat(),
                "started_at": started_at.isoformat(),
            },
        )
        try:
            async with asyncio.timeout(trigger.max_runtime_seconds):
                await self._callback(trigger)
        except TimeoutError:
            await self._record(
                "trigger.timeout",
                {
                    "trigger_id": trigger.id,
                    "max_runtime_seconds": trigger.max_runtime_seconds,
                },
            )
            logger.warning(
                "proactive.trigger_timeout",
                trigger_id=trigger.id,
                max_runtime_seconds=trigger.max_runtime_seconds,
            )
            return
        except Exception as exc:
            await self._record(
                "trigger.error",
                {
                    "trigger_id": trigger.id,
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
            )
            logger.warning(
                "proactive.trigger_error",
                trigger_id=trigger.id,
                error=type(exc).__name__,
                message=str(exc),
            )
            return
        finished_at = self._time_provider()
        duration = (finished_at - started_at).total_seconds()
        await self._record(
            "trigger.completed",
            {
                "trigger_id": trigger.id,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_seconds": duration,
            },
        )

    async def _record(self, event_type: str, payload: dict[str, object]) -> None:
        if self._audit is None:
            return
        await self._audit.record(event_type, payload)
