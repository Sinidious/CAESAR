"""Tests for the notify worker backed by ntfy.sh (ADR-0030, v1.5).

Two layers:

1. Unit tests on :class:`NotifyClient` cover the ntfy.sh HTTP contract:
   JSON request shape, optional auth header, response parsing, error
   mapping. The underlying httpx call is mocked with
   :class:`httpx.MockTransport` so the test never opens a real socket.
2. Handler-level tests verify the worker's input validation and the
   wire-shape contract.

Brain-graph integration coverage (registration + policy gating +
audit) is added in :mod:`tests.test_praetor_graph` alongside the
calculator / web_search / calendar_read tests.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from caesar.legion.notify import (
    CAPABILITY,
    DEFAULT_BASE_URL,
    WORKER_ID,
    NotifyClient,
    NotifyError,
    NotifyWorker,
    _normalise_time,
)
from caesar.legion.protocol import TaskDispatch


def _client(
    handler: httpx.MockTransport,
    *,
    base_url: str = DEFAULT_BASE_URL,
    topic: str = "caesar-home",
    token: str | None = None,
) -> NotifyClient:
    return NotifyClient(
        base_url=base_url,
        topic=topic,
        token=token,
        timeout_seconds=2.0,
        http=httpx.AsyncClient(transport=handler, timeout=2.0),
    )


def _ok_response(*, msg_id: str = "abc123", time: int | None = 1779019200) -> httpx.Response:
    body: dict[str, Any] = {"id": msg_id, "topic": "caesar-home"}
    if time is not None:
        body["time"] = time
    return httpx.Response(200, content=json.dumps(body))


# --- NotifyClient happy path ------------------------------------------------


async def test_client_publishes_with_default_shape() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ok_response()

    client = _client(httpx.MockTransport(handler))
    try:
        result = await client.publish(
            title="hello",
            message="world",
            priority=3,
        )
    finally:
        await client.aclose()
    assert result == {
        "id": "abc123",
        "delivered_at": "2026-05-17T12:00:00+00:00",
    }
    assert len(captured) == 1
    body = json.loads(captured[0].content.decode())
    assert body == {
        "topic": "caesar-home",
        "title": "hello",
        "message": "world",
        "priority": 3,
    }
    # No Authorization header without a token.
    assert "Authorization" not in captured[0].headers


async def test_client_sends_tags_when_present() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ok_response()

    client = _client(httpx.MockTransport(handler))
    try:
        await client.publish(
            title="t",
            message="m",
            priority=2,
            tags=["sunny", "weather"],
        )
    finally:
        await client.aclose()
    body = json.loads(captured[0].content.decode())
    assert body["tags"] == ["sunny", "weather"]


async def test_client_sends_bearer_header_when_token_set() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ok_response()

    client = _client(httpx.MockTransport(handler), token="secret-token")
    try:
        await client.publish(title="t", message="m", priority=3)
    finally:
        await client.aclose()
    assert captured[0].headers["Authorization"] == "Bearer secret-token"


async def test_client_strips_trailing_slash_in_base_url() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ok_response()

    client = _client(httpx.MockTransport(handler), base_url="https://ntfy.example/")
    try:
        await client.publish(title="t", message="m", priority=3)
    finally:
        await client.aclose()
    assert str(captured[0].url) == "https://ntfy.example"


async def test_client_falls_back_to_now_when_time_missing() -> None:
    transport = httpx.MockTransport(lambda req: _ok_response(time=None))
    client = _client(transport)
    try:
        result = await client.publish(title="t", message="m", priority=3)
    finally:
        await client.aclose()
    # Couldn't predict the exact wall-clock value, but it should be a
    # parseable ISO-8601 string ending in +00:00.
    assert result["delivered_at"].endswith("+00:00")


# --- NotifyClient error paths -----------------------------------------------


async def test_client_raises_on_http_5xx() -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(503, content="upstream timeout"),
    )
    client = _client(transport)
    try:
        with pytest.raises(NotifyError, match="HTTP 503"):
            await client.publish(title="t", message="m", priority=3)
    finally:
        await client.aclose()


async def test_client_raises_on_non_json_body() -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content="not json"),
    )
    client = _client(transport)
    try:
        with pytest.raises(NotifyError, match="non-JSON body"):
            await client.publish(title="t", message="m", priority=3)
    finally:
        await client.aclose()


async def test_client_raises_on_non_object_body() -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=json.dumps([1, 2, 3])),
    )
    client = _client(transport)
    try:
        with pytest.raises(NotifyError, match="JSON object"):
            await client.publish(title="t", message="m", priority=3)
    finally:
        await client.aclose()


async def test_client_raises_when_id_missing() -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=json.dumps({"topic": "x"})),
    )
    client = _client(transport)
    try:
        with pytest.raises(NotifyError, match="missing 'id'"):
            await client.publish(title="t", message="m", priority=3)
    finally:
        await client.aclose()


async def test_client_maps_httpx_transport_errors() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    transport = httpx.MockTransport(boom)
    client = _client(transport)
    try:
        with pytest.raises(NotifyError, match="ntfy request failed"):
            await client.publish(title="t", message="m", priority=3)
    finally:
        await client.aclose()


# --- _normalise_time helper -------------------------------------------------


def test_normalise_time_int() -> None:
    out = _normalise_time(1779019200)
    assert out == "2026-05-17T12:00:00+00:00"


def test_normalise_time_float() -> None:
    out = _normalise_time(1779019200.5)
    assert out.startswith("2026-05-17T12:00:00")


def test_normalise_time_bool_rejected() -> None:
    """``True`` is an int subclass; we don't want it to slip through."""

    out = _normalise_time(True)
    # Fallback to wall clock; far from 1970.
    assert not out.startswith("1970")


def test_normalise_time_missing() -> None:
    out = _normalise_time(None)
    assert out.endswith("+00:00")


# --- Worker handler contract ------------------------------------------------


def test_worker_metadata() -> None:
    assert NotifyWorker.worker_id == WORKER_ID == "notify"
    assert NotifyWorker.capabilities == [CAPABILITY] == ["tool.notify"]


def test_worker_rejects_bad_default_priority() -> None:
    with pytest.raises(ValueError, match="default_priority"):
        NotifyWorker(
            bus=None,  # type: ignore[arg-type]
            client=_FakeClient(),  # type: ignore[arg-type]
            default_priority=99,
        )


class _FakeClient:
    """Captures publish arguments and returns a canned id."""

    def __init__(self, *, fail: NotifyError | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail = fail

    async def publish(
        self,
        *,
        title: str,
        message: str,
        priority: int,
        tags: list[str] | None = None,
    ) -> dict[str, str]:
        self.calls.append(
            {
                "title": title,
                "message": message,
                "priority": priority,
                "tags": tags,
            }
        )
        if self._fail is not None:
            raise self._fail
        return {"id": "fake-id", "delivered_at": "2026-05-17T12:00:00+00:00"}

    async def aclose(self) -> None:
        return None


async def test_handle_returns_id_and_timestamp() -> None:
    client = _FakeClient()
    worker = NotifyWorker(bus=None, client=client)  # type: ignore[arg-type]
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"title": "hi", "message": "there", "priority": 4, "tags": ["sun"]},
    )
    out = await worker.handle(task)
    assert out == {"id": "fake-id", "delivered_at": "2026-05-17T12:00:00+00:00"}
    assert client.calls[0] == {
        "title": "hi",
        "message": "there",
        "priority": 4,
        "tags": ["sun"],
    }


async def test_handle_uses_default_priority_when_missing() -> None:
    client = _FakeClient()
    worker = NotifyWorker(
        bus=None,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        default_priority=2,
    )
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"title": "t", "message": "m"},
    )
    await worker.handle(task)
    assert client.calls[0]["priority"] == 2


async def test_handle_uses_default_priority_when_explicit_none() -> None:
    client = _FakeClient()
    worker = NotifyWorker(
        bus=None,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        default_priority=4,
    )
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"title": "t", "message": "m", "priority": None},
    )
    await worker.handle(task)
    assert client.calls[0]["priority"] == 4


async def test_handle_omits_tags_when_empty() -> None:
    client = _FakeClient()
    worker = NotifyWorker(bus=None, client=client)  # type: ignore[arg-type]
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"title": "t", "message": "m"},
    )
    await worker.handle(task)
    assert client.calls[0]["tags"] is None


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"message": "m"}, "title"),
        ({"title": "", "message": "m"}, "title"),
        ({"title": 42, "message": "m"}, "title"),
        ({"title": "t"}, "message"),
        ({"title": "t", "message": ""}, "message"),
        ({"title": "t", "message": 42}, "message"),
        ({"title": "t", "message": "m", "priority": "huh"}, "priority"),
        ({"title": "t", "message": "m", "priority": 99}, "priority"),
        ({"title": "t", "message": "m", "priority": 0}, "priority"),
        ({"title": "t", "message": "m", "tags": "not-a-list"}, "tags"),
        ({"title": "t", "message": "m", "tags": list(range(11))}, "tags"),
    ],
)
async def test_handle_rejects_bad_input(payload: dict[str, Any], match: str) -> None:
    worker = NotifyWorker(bus=None, client=_FakeClient())  # type: ignore[arg-type]
    task = TaskDispatch(task_id="t1", capability=CAPABILITY, payload=payload)
    with pytest.raises(ValueError, match=match):
        await worker.handle(task)


async def test_handle_rejects_oversized_title() -> None:
    worker = NotifyWorker(bus=None, client=_FakeClient())  # type: ignore[arg-type]
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"title": "x" * 201, "message": "m"},
    )
    with pytest.raises(ValueError, match="at most 200"):
        await worker.handle(task)


async def test_handle_rejects_oversized_message() -> None:
    worker = NotifyWorker(bus=None, client=_FakeClient())  # type: ignore[arg-type]
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"title": "t", "message": "x" * 4097},
    )
    with pytest.raises(ValueError, match="at most 4096"):
        await worker.handle(task)


async def test_handle_propagates_client_error_as_value_error() -> None:
    worker = NotifyWorker(
        bus=None,  # type: ignore[arg-type]
        client=_FakeClient(fail=NotifyError("upstream choked")),  # type: ignore[arg-type]
    )
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"title": "t", "message": "m"},
    )
    with pytest.raises(ValueError, match="upstream choked"):
        await worker.handle(task)


async def test_handle_aclose_passes_through() -> None:
    client = _FakeClient()
    worker = NotifyWorker(bus=None, client=client)  # type: ignore[arg-type]
    await worker.aclose()  # must not raise
