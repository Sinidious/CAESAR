"""Tests for the calendar-read worker (ADR-0028, v1.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from caesar.legion.calendar_read import (
    CAPABILITY,
    DEFAULT_RANGE_DAYS,
    WORKER_ID,
    CalendarReadError,
    CalendarReadWorker,
    _iso,
    _parse_dt,
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
        await worker.handle(
            TaskDispatch(task_id="t", capability=CAPABILITY, payload={})
        )
