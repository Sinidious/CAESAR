"""Long-lived HA event subscription with reconnect (ADR-0031).

The HA bridge's :func:`HAClient.subscribe_events` is a one-shot async
generator: when the WS connection drops, the iterator ends. v1.6's
HA-event triggers need the opposite — a subscription that *stays*
open across HA restarts, network blips, and Praetor's own pauses.

:class:`ResilientHAEventStream` is that wrapper. It:

- Owns a long-lived asyncio task that keeps the subscription open.
- Reconnects with exponential backoff + full jitter when the
  generator ends (any cause: HA restart, network drop, idle timeout).
- Drops events during disconnect (per ADR-0031 §2 — replay is HA's
  job, not ours; webhook source in v1.7 is the reliability story).
- Audit-logs ``ha.subscription.opened`` once on first successful
  connect; ``ha.subscription.reconnected`` on every subsequent
  connect; ``ha.subscription.closed`` on stop or unrecoverable error.
- Hands each incoming event to an injected callback. Callback
  exceptions are caught + audited; the stream keeps going.

The stream is independent of the trigger model. v1.6's
``HAEventDriver`` builds on top of this and adds per-trigger
matching, cooldown, and brain dispatch. This module is just the
transport.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable
from typing import Any

from caesar.db.audit import AuditLogger
from caesar.ha.client import HAClient
from caesar.log import get_logger

logger = get_logger("caesar.ha.subscription")

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

DEFAULT_INITIAL_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 60.0


class ResilientHAEventStream:
    """Long-lived HA WS subscription with reconnect + audit."""

    def __init__(
        self,
        ha: HAClient,
        callback: EventCallback,
        *,
        event_type: str | None = None,
        audit: AuditLogger | None = None,
        initial_backoff_seconds: float = DEFAULT_INITIAL_BACKOFF_SECONDS,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._ha = ha
        self._callback = callback
        self._event_type = event_type
        self._audit = audit
        self._initial_backoff = initial_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._connect_count = 0
        self._announced_this_connect = False

    @property
    def connect_count(self) -> int:
        return self._connect_count

    async def start(self) -> None:
        """Spawn the background task. Idempotent."""

        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="caesar.ha.subscription")

    async def stop(self) -> None:
        """Signal the loop to exit and join the task."""

        self._stop.set()
        task = self._task
        if task is None:
            return
        self._task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await self._record_closed(reason="stopped")

    async def _run(self) -> None:
        """Connect, consume, reconnect — until stop is signalled."""

        backoff = self._initial_backoff
        while not self._stop.is_set():
            try:
                await self._consume_one_subscription()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "ha.subscription.error",
                    error=type(exc).__name__,
                    message=str(exc),
                    next_backoff_seconds=backoff,
                )
            else:
                # The generator ended without exception — the WS closed
                # cleanly (HA restart, idle timeout). Treat the same as
                # a transient error: back off and reconnect.
                logger.info(
                    "ha.subscription.generator_ended",
                    next_backoff_seconds=backoff,
                )
            if self._stop.is_set():  # pragma: no cover - cancel races
                break
            await self._sleep_with_jitter(backoff)
            backoff = min(backoff * 2, self._max_backoff)

    async def _consume_one_subscription(self) -> None:
        """One subscription lifetime: connect, audit, consume, loop ends."""

        try:
            async for event in self._ha.subscribe_events(self._event_type):
                # The first event proves the subscription is up — emit
                # opened/reconnected audit row exactly once per connect.
                if not self._announced_this_connect:
                    self._connect_count += 1
                    if self._connect_count == 1:
                        await self._record_opened()
                    else:
                        await self._record_reconnected()
                    self._announced_this_connect = True
                try:
                    await self._callback(event)
                except asyncio.CancelledError:  # pragma: no cover - cancel races
                    raise
                except Exception as exc:
                    logger.warning(
                        "ha.subscription.callback_error",
                        error=type(exc).__name__,
                        message=str(exc),
                    )
                    await self._record_callback_error(exc)
                if self._stop.is_set():  # pragma: no cover - cancel races
                    return
        finally:
            # Reset for the next connect so it announces afresh.
            self._announced_this_connect = False

    async def _sleep_with_jitter(self, base: float) -> None:
        # Full jitter: sleep a random amount in [0, base]. Standard
        # AWS-style retry shape; spreads thundering-herd reconnects.
        jittered = random.uniform(0.0, base)
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=jittered)
        except TimeoutError:
            return

    async def _record_opened(self) -> None:
        logger.info("ha.subscription.opened", event_type=self._event_type)
        if self._audit is not None:
            await self._audit.record(
                "ha.subscription.opened",
                {"event_type": self._event_type},
            )

    async def _record_reconnected(self) -> None:
        logger.info(
            "ha.subscription.reconnected",
            event_type=self._event_type,
            connect_count=self._connect_count,
        )
        if self._audit is not None:
            await self._audit.record(
                "ha.subscription.reconnected",
                {
                    "event_type": self._event_type,
                    "connect_count": self._connect_count,
                },
            )

    async def _record_closed(self, *, reason: str) -> None:
        logger.info("ha.subscription.closed", reason=reason)
        if self._audit is not None:
            await self._audit.record(
                "ha.subscription.closed",
                {"reason": reason, "connect_count": self._connect_count},
            )

    async def _record_callback_error(self, exc: BaseException) -> None:
        if self._audit is None:  # pragma: no cover - tests always pass audit
            return
        await self._audit.record(
            "ha.subscription.callback_error",
            {
                "error": type(exc).__name__,
                "message": str(exc),
            },
        )
