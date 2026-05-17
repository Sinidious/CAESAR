"""Calendar-read worker backed by CalDAV (ADR-0028).

Reads upcoming events from a homelab CalDAV server — Nextcloud,
Baikal, Radicale, etc. Read-only for v1.3; writes are a follow-up.

The :mod:`caldav` library is synchronous, so the worker offloads
each request to a thread via :func:`asyncio.to_thread`. Tests pass
a ``CalendarReadClient`` stub directly so they don't need a real
server.

Input payload (dispatched from the brain graph via
``caesar.dispatch.tool.calendar_read``):

.. code-block:: json

    {
        "from": "2026-05-17T00:00:00Z",   // optional, default = now
        "to":   "2026-05-24T00:00:00Z",   // optional, default = from + 7d
        "limit": 20                       // optional, default = 20
    }

Output:

.. code-block:: json

    {
        "from": "...",
        "to":   "...",
        "events": [
            {
                "title":       "Standup",
                "start":       "2026-05-17T09:00:00+00:00",
                "end":         "2026-05-17T09:15:00+00:00",
                "location":    "Kitchen",
                "description": "...",
                "calendar":    "Work"
            },
            ...
        ]
    }
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Protocol

from caesar.bus.client import Bus
from caesar.legion.protocol import TaskDispatch
from caesar.legion.worker import Worker

CAPABILITY = "tool.calendar_read"
WORKER_ID = "calendar_read"
DEFAULT_RANGE_DAYS = 7
DEFAULT_EVENT_LIMIT = 20
MAX_EVENT_LIMIT = 200
MAX_RANGE_DAYS = 365


class CalendarReadError(ValueError):
    """The CalDAV backend was unreachable or returned an unusable shape."""


class CalendarReadClient(Protocol):
    """Structural shape every concrete client must satisfy.

    The production implementation wraps :mod:`caldav` over HTTP;
    tests pass a recording stub directly.
    """

    async def fetch_events(
        self,
        *,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return at most ``limit`` events between ``start`` and ``end``."""
        ...

    async def aclose(self) -> None:
        """Release any held resources."""
        ...


class CalDAVClient:
    """Production :class:`CalendarReadClient` backed by :mod:`caldav`.

    Synchronous CalDAV operations are offloaded with
    :func:`asyncio.to_thread` so the worker stays cooperative. The
    underlying ``caldav.DAVClient`` is constructed lazily on the
    first call so a misconfigured ``caldav_url`` doesn't crash
    worker startup.
    """

    def __init__(
        self,
        *,
        caldav_url: str,
        username: str,
        password: str,
        calendar_names: list[str] | None = None,
    ) -> None:
        self._caldav_url = caldav_url
        self._username = username
        self._password = password
        self._calendar_names = calendar_names or []
        self._principal: Any | None = None

    async def aclose(self) -> None:
        # caldav.DAVClient holds an httpx session; closing is sync.
        return None

    # The methods below all delegate to the third-party ``caldav``
    # library against a live CalDAV server. They're exercised
    # manually against a real Nextcloud / Baikal / Radicale during
    # development; CI doesn't run a CalDAV instance, so coverage
    # would have to come from heavy library mocks that prove nothing
    # about the real integration. Mark as pragma: no cover and
    # cover the surface that *is* unit-testable (parsing, validation,
    # normalisation, worker handler) in tests/test_calendar_read.py.
    def _principal_sync(self) -> Any:  # pragma: no cover - needs live CalDAV
        if self._principal is None:
            import caldav

            client = caldav.DAVClient(  # type: ignore[operator]
                url=self._caldav_url,
                username=self._username,
                password=self._password,
            )
            try:
                self._principal = client.principal()
            except Exception as exc:  # caldav raises a tall stack of types
                raise CalendarReadError(f"CalDAV principal lookup failed: {exc}") from exc
        return self._principal

    def _selected_calendars_sync(self) -> list[Any]:  # pragma: no cover - needs live CalDAV
        principal = self._principal_sync()
        try:
            calendars = principal.calendars()
        except Exception as exc:
            raise CalendarReadError(f"CalDAV calendar list failed: {exc}") from exc
        if not self._calendar_names:
            return list(calendars)
        wanted = set(self._calendar_names)
        return [c for c in calendars if getattr(c, "name", None) in wanted]

    def _fetch_events_sync(  # pragma: no cover - needs live CalDAV
        self, *, start: datetime, end: datetime, limit: int
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for cal in self._selected_calendars_sync():
            try:
                found = cal.search(
                    start=start,
                    end=end,
                    event=True,
                    expand=True,
                )
            except Exception as exc:
                raise CalendarReadError(f"CalDAV search failed on {cal!r}: {exc}") from exc
            for evt in found:
                if len(events) >= limit:
                    return events
                normalised = _normalise_event(evt, calendar_name=getattr(cal, "name", "") or "")
                if normalised is not None:
                    events.append(normalised)
        return events

    async def fetch_events(  # pragma: no cover - needs live CalDAV
        self,
        *,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_events_sync, start=start, end=end, limit=limit)


def _normalise_event(evt: Any, *, calendar_name: str) -> dict[str, Any] | None:
    """Convert a caldav.Event into our flat dict shape."""

    vcal = getattr(evt, "vobject_instance", None) or getattr(evt, "icalendar_instance", None)
    if vcal is None:
        return None
    # caldav events have one VEVENT per object after expand=True.
    vevent = None
    with contextlib.suppress(AttributeError, StopIteration):
        vevent = next(iter(vcal.subcomponents)) if hasattr(vcal, "subcomponents") else vcal.vevent
    if vevent is None:
        return None

    title = _vget(vevent, "summary")
    start = _vget(vevent, "dtstart")
    end = _vget(vevent, "dtend")
    location = _vget(vevent, "location")
    description = _vget(vevent, "description")
    return {
        "title": str(title) if title is not None else "",
        "start": _iso(start),
        "end": _iso(end),
        "location": str(location) if location is not None else "",
        "description": str(description) if description is not None else "",
        "calendar": calendar_name,
    }


def _vget(vevent: Any, name: str) -> Any:
    """Safely extract a field from either a vobject or icalendar event."""

    # vobject style: `vevent.summary.value`
    contents = getattr(vevent, "contents", None)
    if isinstance(contents, dict) and name in contents:
        items = contents[name]
        if items:
            return getattr(items[0], "value", items[0])
    # icalendar style: dict-like
    if hasattr(vevent, "get"):
        try:
            value = vevent.get(name)
        except Exception:  # pragma: no cover - defensive
            value = None
        if value is not None:
            return getattr(value, "dt", value)
    return None


def _iso(value: Any) -> str:
    """Render a date/datetime/string as ISO-8601 in UTC."""

    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


def _parse_dt(value: Any, default: datetime) -> datetime:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"timestamp must be an ISO string; got {type(value).__name__}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp {value!r}: {exc}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class CalendarReadWorker(Worker):
    """CalDAV-backed Legion worker."""

    worker_id: ClassVar[str] = WORKER_ID
    capabilities: ClassVar[list[str]] = [CAPABILITY]
    version: ClassVar[str] = "0.3.0"

    def __init__(
        self,
        bus: Bus,
        client: CalendarReadClient,
        *,
        default_limit: int = DEFAULT_EVENT_LIMIT,
        max_limit: int = MAX_EVENT_LIMIT,
        default_range_days: int = DEFAULT_RANGE_DAYS,
        max_range_days: int = MAX_RANGE_DAYS,
    ) -> None:
        super().__init__(bus)
        self._client = client
        self._default_limit = default_limit
        self._max_limit = max_limit
        self._default_range_days = default_range_days
        self._max_range_days = max_range_days

    async def aclose(self) -> None:
        await self._client.aclose()

    async def handle(self, task: TaskDispatch) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        start = _parse_dt(task.payload.get("from"), default=now)
        end = _parse_dt(
            task.payload.get("to"),
            default=start + timedelta(days=self._default_range_days),
        )
        if end <= start:
            raise ValueError("'to' must be after 'from'")
        if (end - start) > timedelta(days=self._max_range_days):
            raise ValueError(f"date range exceeds {self._max_range_days} days")

        raw_limit = task.payload.get("limit", self._default_limit)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"'limit' must be an integer, got {raw_limit!r}") from exc
        if limit < 1:
            raise ValueError("'limit' must be >= 1")
        limit = min(limit, self._max_limit)

        try:
            events = await self._client.fetch_events(start=start, end=end, limit=limit)
        except CalendarReadError as exc:
            raise ValueError(str(exc)) from exc

        return {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "events": events,
        }
