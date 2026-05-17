"""Tests for the calendar-read worker (ADR-0028, v1.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest

from caesar.legion.calendar_read import (
    CAPABILITY,
    DEFAULT_RANGE_DAYS,
    WORKER_ID,
    CalDAVClient,
    CalendarReadError,
    CalendarReadWorker,
    _iso,
    _normalise_event,
    _parse_dt,
    _vget,
)
from caesar.legion.protocol import TaskDispatch


def _ev(
    *, title: str, start: datetime, end: datetime, calendar: str = "Personal"
) -> dict[str, Any]:
    return {
        "title": title,
        "start": _iso(start),
        "end": _iso(end),
        "location": "",
        "description": "",
        "calendar": calendar,
    }


class _FakeClient:
    """Records search args; returns canned events."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.calls: list[dict[str, Any]] = []

    async def fetch_events(
        self,
        *,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        self.calls.append({"start": start, "end": end, "limit": limit})
        return self._events[:limit]

    async def aclose(self) -> None:
        return None


# --- _parse_dt --------------------------------------------------------------


def test_parse_dt_returns_default_when_none() -> None:
    default = datetime(2026, 1, 1, tzinfo=UTC)
    assert _parse_dt(None, default=default) is default


def test_parse_dt_handles_z_suffix() -> None:
    parsed = _parse_dt("2026-05-17T09:00:00Z", default=datetime.now(tz=UTC))
    assert parsed == datetime(2026, 5, 17, 9, 0, tzinfo=UTC)


def test_parse_dt_attaches_utc_when_naive() -> None:
    parsed = _parse_dt("2026-05-17T09:00:00", default=datetime.now(tz=UTC))
    assert parsed.tzinfo == UTC


def test_parse_dt_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="must be an ISO string"):
        _parse_dt(123, default=datetime.now(tz=UTC))


def test_parse_dt_rejects_malformed_iso() -> None:
    with pytest.raises(ValueError, match="invalid ISO timestamp"):
        _parse_dt("not-a-date", default=datetime.now(tz=UTC))


# --- worker metadata -------------------------------------------------------


def test_worker_metadata() -> None:
    assert CalendarReadWorker.worker_id == WORKER_ID == "calendar_read"
    assert CalendarReadWorker.capabilities == [CAPABILITY] == ["tool.calendar_read"]


# --- handler contract -------------------------------------------------------


def _worker(client: _FakeClient, **kw: Any) -> CalendarReadWorker:
    return CalendarReadWorker(bus=None, client=client, **kw)  # type: ignore[arg-type]


async def test_handle_defaults_to_now_through_default_range() -> None:
    client = _FakeClient(events=[])
    worker = _worker(client)
    task = TaskDispatch(task_id="t", capability=CAPABILITY, payload={})
    out = await worker.handle(task)

    assert client.calls, "expected one fetch call"
    call = client.calls[0]
    range_days = (call["end"] - call["start"]).days
    assert range_days == DEFAULT_RANGE_DAYS
    assert out["events"] == []
    assert out["from"] == call["start"].isoformat()
    assert out["to"] == call["end"].isoformat()


async def test_handle_honours_explicit_from_and_to() -> None:
    client = _FakeClient(events=[])
    worker = _worker(client)
    task = TaskDispatch(
        task_id="t",
        capability=CAPABILITY,
        payload={
            "from": "2026-05-17T09:00:00Z",
            "to": "2026-05-17T17:00:00Z",
        },
    )
    out = await worker.handle(task)
    call = client.calls[0]
    assert call["start"] == datetime(2026, 5, 17, 9, 0, tzinfo=UTC)
    assert call["end"] == datetime(2026, 5, 17, 17, 0, tzinfo=UTC)
    assert out["from"] == "2026-05-17T09:00:00+00:00"
    assert out["to"] == "2026-05-17T17:00:00+00:00"


async def test_handle_returns_normalised_events() -> None:
    start = datetime(2026, 5, 17, 9, 0, tzinfo=UTC)
    end = start + timedelta(minutes=30)
    client = _FakeClient(events=[_ev(title="Standup", start=start, end=end)])
    worker = _worker(client)
    out = await worker.handle(
        TaskDispatch(
            task_id="t",
            capability=CAPABILITY,
            payload={"from": "2026-05-17T00:00:00Z", "to": "2026-05-18T00:00:00Z"},
        )
    )
    assert len(out["events"]) == 1
    assert out["events"][0]["title"] == "Standup"


async def test_handle_clamps_limit() -> None:
    client = _FakeClient(events=[])
    worker = _worker(client, default_limit=5, max_limit=10)
    await worker.handle(
        TaskDispatch(
            task_id="t",
            capability=CAPABILITY,
            payload={"limit": 9999},
        )
    )
    assert client.calls[0]["limit"] == 10


async def test_handle_rejects_bad_limit() -> None:
    worker = _worker(_FakeClient(events=[]))
    with pytest.raises(ValueError, match="must be an integer"):
        await worker.handle(
            TaskDispatch(
                task_id="t",
                capability=CAPABILITY,
                payload={"limit": "many"},
            )
        )


async def test_handle_rejects_to_before_from() -> None:
    worker = _worker(_FakeClient(events=[]))
    with pytest.raises(ValueError, match="must be after"):
        await worker.handle(
            TaskDispatch(
                task_id="t",
                capability=CAPABILITY,
                payload={"from": "2026-05-17T10:00:00Z", "to": "2026-05-17T09:00:00Z"},
            )
        )


async def test_handle_rejects_range_too_large() -> None:
    worker = _worker(_FakeClient(events=[]), max_range_days=30)
    with pytest.raises(ValueError, match="exceeds 30 days"):
        await worker.handle(
            TaskDispatch(
                task_id="t",
                capability=CAPABILITY,
                payload={
                    "from": "2026-01-01T00:00:00Z",
                    "to": "2026-06-01T00:00:00Z",
                },
            )
        )


# --- _iso / _vget / _normalise_event ---------------------------------------


def test_iso_handles_none_and_naive_datetimes() -> None:
    assert _iso(None) == ""
    naive = datetime(2026, 5, 17, 9, 0)
    assert _iso(naive).endswith("+00:00")
    assert _iso("2026-05-17") == "2026-05-17"


class _VObjectLeaf:
    def __init__(self, value: object) -> None:
        self.value = value


def test_vget_vobject_style_returns_value() -> None:
    class _VEvent:
        contents: ClassVar[dict[str, list[object]]] = {"summary": [_VObjectLeaf("Standup")]}

    assert _vget(_VEvent(), "summary") == "Standup"


def test_vget_icalendar_style_returns_dt() -> None:
    class _Wrapper:
        dt = datetime(2026, 5, 17, 9, 0, tzinfo=UTC)

    class _VEvent:
        def get(self, name: str) -> object | None:
            return _Wrapper() if name == "dtstart" else None

    assert _vget(_VEvent(), "dtstart") == datetime(2026, 5, 17, 9, 0, tzinfo=UTC)


def test_vget_returns_none_for_unknown_field() -> None:
    class _VEvent:
        contents: ClassVar[dict[str, list[object]]] = {}

        def get(self, name: str) -> None:
            return None

    assert _vget(_VEvent(), "anything") is None


def test_normalise_event_vobject_style_returns_flat_dict() -> None:
    class _SubComponent:
        contents: ClassVar[dict[str, list[object]]] = {
            "summary": [_VObjectLeaf("Standup")],
            "dtstart": [_VObjectLeaf(datetime(2026, 5, 17, 9, 0, tzinfo=UTC))],
            "dtend": [_VObjectLeaf(datetime(2026, 5, 17, 9, 15, tzinfo=UTC))],
            "location": [_VObjectLeaf("Kitchen")],
            "description": [_VObjectLeaf("notes")],
        }

    class _VCal:
        subcomponents: ClassVar[list[_SubComponent]] = [_SubComponent()]

    class _Event:
        vobject_instance: ClassVar[_VCal] = _VCal()

    out = _normalise_event(_Event(), calendar_name="Work")
    assert out == {
        "title": "Standup",
        "start": "2026-05-17T09:00:00+00:00",
        "end": "2026-05-17T09:15:00+00:00",
        "location": "Kitchen",
        "description": "notes",
        "calendar": "Work",
    }


def test_normalise_event_returns_none_when_no_vcal() -> None:
    class _Event:
        vobject_instance = None
        icalendar_instance = None

    assert _normalise_event(_Event(), calendar_name="x") is None


# --- CalDAVClient construction (lazy: no live server hit) -------------------


def test_caldav_client_constructible_without_hitting_server() -> None:
    """Lazy principal lookup means we can build the client cheaply."""

    client = CalDAVClient(
        caldav_url="http://localhost:5232/",
        username="user",
        password="pw",
        calendar_names=["Personal"],
    )
    assert client._caldav_url == "http://localhost:5232/"


async def test_caldav_client_aclose_is_safe() -> None:
    client = CalDAVClient(caldav_url="http://x", username="u", password="p")
    await client.aclose()


async def test_handle_translates_client_error_to_value_error() -> None:
    class _ExplodingClient:
        async def fetch_events(
            self, *, start: datetime, end: datetime, limit: int
        ) -> list[dict[str, Any]]:
            raise CalendarReadError("server unreachable")

        async def aclose(self) -> None:
            return None

    worker = CalendarReadWorker(bus=None, client=_ExplodingClient())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="server unreachable"):
        await worker.handle(TaskDispatch(task_id="t", capability=CAPABILITY, payload={}))
