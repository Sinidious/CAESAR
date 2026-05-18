"""HTTP-webhook dispatcher (ADR-0032).

Counterpart to the v1.5 :class:`Scheduler` and v1.6
:class:`HAEventDriver`. Where they own the input transport (cron
clock, HA WebSocket subscription), this dispatcher is **fed by an
HTTP route**: :func:`POST /v1/hook/{trigger_id}` (see
``caesar/praetor/routes/webhook.py``) hands validated requests in.

The dispatcher's job is exactly what the other drivers do post-input:

- Verify the bearer token matches the trigger config (constant-time).
- Enforce per-trigger ``cooldown_seconds`` with coalesced
  :data:`trigger.suppressed` audit rows.
- Format the request body into the brain prompt as user-message
  context ("Event body:\\n<JSON or raw text>").
- Fire :class:`ProactiveRunner` in a background task so the HTTP
  response can return 202 immediately.

The route registers the dispatcher at startup; it doesn't need to
import the dispatcher otherwise. All audit + dispatch logic lives
here so the route stays thin.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from caesar.db.audit import AuditLogger
from caesar.log import get_logger
from caesar.proactive.runner import ProactiveRunner
from caesar.proactive.triggers import Trigger, WebhookSource

logger = get_logger("caesar.proactive.webhook_dispatcher")

# Cap body size at the edge; SR-008 clamps audit-log strings to 16 KiB.
# A 64 KiB body limit means an attacker can't OOM Praetor with one POST,
# and a well-formed sender's body never gets close.
MAX_BODY_BYTES: Final[int] = 64 * 1024


def _now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Suppression:
    """Coalescing state for cooldown-suppressed POSTs on one trigger."""

    count: int
    first_at: datetime
    last_at: datetime


class WebhookDispatcher:
    """Routes inbound POST bodies to :class:`ProactiveRunner` fires."""

    def __init__(
        self,
        triggers: Iterable[Trigger],
        *,
        runner: ProactiveRunner,
        audit: AuditLogger,
        clock: Callable[[], datetime] = _now_utc,
    ) -> None:
        self._runner = runner
        self._audit = audit
        self._clock = clock
        self._registry: dict[str, Trigger] = {
            t.id: t for t in triggers if t.enabled and isinstance(t.source, WebhookSource)
        }
        self._last_fired: dict[str, datetime] = {}
        self._suppressed: dict[str, _Suppression] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def armed_count(self) -> int:
        return len(self._registry)

    def get(self, trigger_id: str) -> Trigger | None:
        """Return the trigger for ``trigger_id``, or ``None``."""

        return self._registry.get(trigger_id)

    def verify_bearer(self, trigger: Trigger, supplied: str | None) -> bool:
        """Constant-time compare of supplied vs configured bearer."""

        assert isinstance(trigger.source, WebhookSource)
        if supplied is None:
            return False
        expected = trigger.source.bearer_token.get_secret_value()
        return hmac.compare_digest(expected, supplied)

    def is_in_cooldown(self, trigger: Trigger) -> bool:
        """True iff the trigger's cooldown is active right now."""

        if trigger.cooldown_seconds is None:
            return False
        last = self._last_fired.get(trigger.id)
        if last is None:
            return False
        elapsed = (self._clock() - last).total_seconds()
        return elapsed < trigger.cooldown_seconds

    # --- public dispatch API used by the FastAPI route -----------------

    async def announce(self) -> None:
        """Emit ``trigger.subscribed`` for every armed webhook trigger.

        Matches the v1.6 HAEventDriver convention so dashboards filter
        the timeline consistently — every proactive trigger says hi at
        startup.
        """

        for trigger in self._registry.values():
            assert isinstance(trigger.source, WebhookSource)
            await self._audit.record(
                "trigger.subscribed",
                {
                    "trigger_id": trigger.id,
                    "source_kind": "webhook",
                    "cooldown_seconds": trigger.cooldown_seconds,
                },
            )

    async def record_received(
        self, trigger: Trigger, *, body_bytes: int, source_ip: str | None
    ) -> None:
        await self._audit.record(
            "webhook.received",
            {
                "trigger_id": trigger.id,
                "body_bytes": body_bytes,
                "source_ip": source_ip,
            },
        )

    async def record_unauthorized(self, trigger_id: str, *, source_ip: str | None) -> None:
        # NEVER log the supplied bearer — that's how leaked tokens end
        # up in dashboard screenshots and bug-report copy-pastes.
        logger.warning(
            "webhook.unauthorized",
            trigger_id=trigger_id,
            source_ip=source_ip,
        )
        await self._audit.record(
            "webhook.unauthorized",
            {
                "trigger_id": trigger_id,
                "source_ip": source_ip,
            },
        )

    async def record_unknown_trigger(self, trigger_id: str, *, source_ip: str | None) -> None:
        logger.warning(
            "webhook.unknown_trigger",
            trigger_id=trigger_id,
            source_ip=source_ip,
        )
        await self._audit.record(
            "webhook.unknown_trigger",
            {
                "trigger_id": trigger_id,
                "source_ip": source_ip,
            },
        )

    def record_suppression(self, trigger_id: str) -> None:
        """Record a cooldown-window suppression. Coalesces with prior ones."""

        now = self._clock()
        existing = self._suppressed.get(trigger_id)
        if existing is None:
            self._suppressed[trigger_id] = _Suppression(count=1, first_at=now, last_at=now)
            return
        existing.count += 1
        existing.last_at = now

    async def fire(self, trigger: Trigger, body: bytes) -> None:
        """Mark the trigger fired and run the brain (intended for asyncio.create_task).

        The HTTP route returns 202 before this completes. Errors land
        in the audit log via :class:`ProactiveRunner` and the brain
        graph; they don't surface to the webhook sender.
        """

        await self._flush_suppression(trigger.id)
        self._last_fired[trigger.id] = self._clock()
        augmented = _trigger_with_body(trigger, body)
        try:
            await self._runner.fire(augmented)
        except asyncio.CancelledError:  # pragma: no cover - shutdown race
            raise
        except Exception as exc:
            logger.warning(
                "webhook.fire_error",
                trigger_id=trigger.id,
                error=type(exc).__name__,
                message=str(exc),
            )
            await self._audit.record(
                "trigger.error",
                {
                    "trigger_id": trigger.id,
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
            )

    def spawn_fire(self, trigger: Trigger, body: bytes) -> None:
        """Schedule :meth:`fire` as a background task.

        Tracked so ``stop()`` can await pending tasks at shutdown.
        """

        task = asyncio.create_task(
            self.fire(trigger, body),
            name=f"caesar.webhook.fire.{trigger.id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        """Flush pending suppressions and await in-flight fires."""

        for trigger_id in list(self._suppressed):
            await self._flush_suppression(trigger_id)
        if self._tasks:
            tasks = list(self._tasks)
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    # --- internals -----------------------------------------------------

    async def _flush_suppression(self, trigger_id: str) -> None:
        pending = self._suppressed.pop(trigger_id, None)
        if pending is None:
            return
        await self._audit.record(
            "trigger.suppressed",
            {
                "trigger_id": trigger_id,
                "count": pending.count,
                "first_event_at": pending.first_at.isoformat(),
                "last_event_at": pending.last_at.isoformat(),
            },
        )


def _trigger_with_body(trigger: Trigger, body: bytes) -> Trigger:
    """Return a copy of ``trigger`` with the body folded into the prompt.

    The brain sees the operator's instructions followed by an "Event
    body:" section. JSON bodies are pretty-printed; non-JSON bodies
    pass through as raw UTF-8 text. SR-008's clamp still applies on
    the audit-log side; the brain itself sees the full body up to
    ``MAX_BODY_BYTES`` (truncated at the route).
    """

    body_text = _format_body(body)
    augmented_prompt = f"{trigger.prompt}\n\nEvent body:\n{body_text}"
    return trigger.model_copy(update={"prompt": augmented_prompt})


def _format_body(body: bytes) -> str:
    if not body:
        return "<empty>"
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        # Non-UTF8 senders shouldn't be hitting CAESAR; surface as-is
        # using `errors="replace"` so the prompt is still readable.
        return body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return text
    return json.dumps(parsed, indent=2, sort_keys=True)
