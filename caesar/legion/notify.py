"""Notification worker backed by ntfy.sh (ADR-0030).

[ntfy.sh](https://ntfy.sh) is an open-source pub-sub HTTP API with iOS
and Android apps. The operator subscribes to a topic; CAESAR publishes
to it. v1.5's primary use case is proactive notifications kicked off by
the scheduler, but the worker is wired as a generic Legion tool so the
brain can also call it at the end of a reactive ``/v1/chat`` turn.

Default settings target the public ntfy.sh server; the operator can
self-host and point ``base_url`` at their own instance. Authentication
is optional — public ntfy.sh topics are unauthenticated by design.

Input payload (dispatched from the brain graph via
``caesar.dispatch.tool.notify``):

.. code-block:: json

    {
        "title": "Morning brief",
        "message": "Calendar is empty until 11am; weather 65/sunny.",
        "priority": 3,
        "tags": ["sunny"]
    }

Output:

.. code-block:: json

    {"id": "ntfy-message-id", "delivered_at": "2026-05-17T07:00:01+00:00"}
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from caesar.bus.client import Bus
from caesar.legion.protocol import TaskDispatch
from caesar.legion.worker import Worker
from caesar.log import get_logger

CAPABILITY = "tool.notify"
WORKER_ID = "notify"

DEFAULT_BASE_URL = "https://ntfy.sh"
DEFAULT_PRIORITY = 3
MIN_PRIORITY = 1
MAX_PRIORITY = 5
MAX_TITLE_CHARS = 200
MAX_MESSAGE_CHARS = 4096
MAX_TAGS = 10
DEFAULT_TIMEOUT_SECONDS = 10.0

logger = get_logger("caesar.legion.notify")


class NotifyError(ValueError):
    """The ntfy backend was unreachable or returned an unusable shape."""


class NotifyClient:
    """Thin ntfy.sh HTTP client.

    Wraps :class:`httpx.AsyncClient` so tests can inject a custom
    transport without monkey-patching the worker. Caller owns the
    httpx client's lifecycle via :meth:`aclose`.
    """

    def __init__(
        self,
        *,
        base_url: str,
        topic: str,
        token: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._topic = topic
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._http = http or httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def publish(
        self,
        *,
        title: str,
        message: str,
        priority: int,
        tags: list[str] | None = None,
    ) -> dict[str, str]:
        """POST one notification. Returns ``{id, delivered_at}``.

        Raises :class:`NotifyError` if the request fails or the response
        isn't the expected ntfy.sh JSON shape.
        """

        body: dict[str, Any] = {
            "topic": self._topic,
            "title": title,
            "message": message,
            "priority": priority,
        }
        if tags:
            body["tags"] = tags
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            resp = await self._http.post(self._base_url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise NotifyError(f"ntfy request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise NotifyError(f"ntfy returned HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise NotifyError(f"ntfy returned non-JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise NotifyError("ntfy response body was not a JSON object.")
        msg_id = str(data.get("id") or "")
        if not msg_id:
            raise NotifyError("ntfy response missing 'id'.")
        delivered_at = _normalise_time(data.get("time"))
        return {"id": msg_id, "delivered_at": delivered_at}


def _normalise_time(raw: Any) -> str:
    """Turn ntfy's unix-seconds ``time`` field into an ISO-8601 UTC string.

    Falls back to "now" when the response omits or mangles the field.
    """

    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return datetime.fromtimestamp(float(raw), tz=UTC).isoformat()
    return datetime.now(UTC).isoformat()


class NotifyWorker(Worker):
    """Notification Legion worker backed by ntfy.sh."""

    worker_id: ClassVar[str] = WORKER_ID
    capabilities: ClassVar[list[str]] = [CAPABILITY]
    version: ClassVar[str] = "0.5.0"

    def __init__(
        self,
        bus: Bus,
        client: NotifyClient,
        *,
        default_priority: int = DEFAULT_PRIORITY,
    ) -> None:
        super().__init__(bus)
        self._client = client
        if not MIN_PRIORITY <= default_priority <= MAX_PRIORITY:
            raise ValueError(
                f"default_priority must be in [{MIN_PRIORITY}, {MAX_PRIORITY}], "
                f"got {default_priority}"
            )
        self._default_priority = default_priority

    async def aclose(self) -> None:
        await self._client.aclose()

    async def handle(self, task: TaskDispatch) -> dict[str, Any]:
        title = _require_text(task.payload.get("title"), "title", MAX_TITLE_CHARS)
        message = _require_text(task.payload.get("message"), "message", MAX_MESSAGE_CHARS)

        raw_priority = task.payload.get("priority", self._default_priority)
        if raw_priority is None:
            priority = self._default_priority
        else:
            try:
                priority = int(raw_priority)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"'priority' must be an integer, got {raw_priority!r}") from exc
        if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
            raise ValueError(f"'priority' must be between {MIN_PRIORITY} and {MAX_PRIORITY}")

        tags_raw = task.payload.get("tags", [])
        if not isinstance(tags_raw, list):
            raise ValueError("'tags' must be a list of strings")
        if len(tags_raw) > MAX_TAGS:
            raise ValueError(f"'tags' must contain at most {MAX_TAGS} entries")
        tags = [str(t) for t in tags_raw]

        try:
            return await self._client.publish(
                title=title,
                message=message,
                priority=priority,
                tags=tags or None,
            )
        except NotifyError as exc:
            raise ValueError(str(exc)) from exc


def _require_text(value: Any, field: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field}' must be a non-empty string")
    if len(value) > max_chars:
        raise ValueError(f"'{field}' must be at most {max_chars} characters")
    return value
