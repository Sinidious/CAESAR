from __future__ import annotations

import pytest

from caesar.ha.client import HAAuthError, HAClient, HAError
from tests.fakeha import VALID_TOKEN, FakeHAWebSocket


async def test_subscribe_events_yields_event() -> None:
    payload = {"event_type": "state_changed", "data": {"entity_id": "light.kitchen"}}
    async with FakeHAWebSocket(events=[payload]) as fake:
        client = HAClient(url=fake.http_url, token=VALID_TOKEN)
        try:
            stream = client.subscribe_events(event_type="state_changed")
            event = await stream.__anext__()
            assert event == payload
        finally:
            await stream.aclose()
            await client.aclose()


async def test_subscribe_events_accepts_no_filter() -> None:
    async with FakeHAWebSocket(events=[{"event_type": "x", "data": {}}]) as fake:
        client = HAClient(url=fake.http_url, token=VALID_TOKEN)
        try:
            stream = client.subscribe_events()
            event = await stream.__anext__()
            assert event["event_type"] == "x"
        finally:
            await stream.aclose()
            await client.aclose()


async def test_bad_token_raises_auth_error() -> None:
    async with FakeHAWebSocket() as fake:
        client = HAClient(url=fake.http_url, token="wrong-token")
        try:
            stream = client.subscribe_events()
            with pytest.raises(HAAuthError):
                await stream.__anext__()
        finally:
            await client.aclose()


def test_ws_url_swaps_http_scheme() -> None:
    plain = HAClient(url="http://ha.test", token="t")
    assert plain._ws_url() == "ws://ha.test/api/websocket"
    secure = HAClient(url="https://ha.test", token="t")
    assert secure._ws_url() == "wss://ha.test/api/websocket"


async def test_unexpected_hello_raises() -> None:
    """A first frame that isn't ``auth_required`` is a protocol error."""

    async with FakeHAWebSocket(wrong_hello=True) as fake:
        client = HAClient(url=fake.http_url, token=VALID_TOKEN)
        try:
            stream = client.subscribe_events()
            with pytest.raises(HAError) as exc:
                await stream.__anext__()
            assert "unexpected hello" in str(exc.value)
        finally:
            await client.aclose()


async def test_failed_subscribe_raises() -> None:
    async with FakeHAWebSocket(fail_subscribe=True) as fake:
        client = HAClient(url=fake.http_url, token=VALID_TOKEN)
        try:
            stream = client.subscribe_events()
            with pytest.raises(HAError) as exc:
                await stream.__anext__()
            assert "subscribe_events failed" in str(exc.value)
        finally:
            await client.aclose()


async def test_non_event_messages_are_skipped() -> None:
    """A pong/result message between events must not be yielded."""

    async with FakeHAWebSocket(
        noise_messages=[{"type": "pong"}],
        events=[{"event_type": "real", "data": {}}],
    ) as fake:
        client = HAClient(url=fake.http_url, token=VALID_TOKEN)
        try:
            stream = client.subscribe_events()
            event = await stream.__anext__()
            assert event["event_type"] == "real"
        finally:
            await stream.aclose()
            await client.aclose()


async def test_iterator_exits_cleanly_when_server_closes() -> None:
    """When HA closes the WS, the iterator raises StopAsyncIteration."""

    async with FakeHAWebSocket(
        events=[{"event_type": "first", "data": {}}],
        close_after_events=True,
    ) as fake:
        client = HAClient(url=fake.http_url, token=VALID_TOKEN)
        try:
            stream = client.subscribe_events()
            assert (await stream.__anext__())["event_type"] == "first"
            with pytest.raises(StopAsyncIteration):
                await stream.__anext__()
        finally:
            await client.aclose()
