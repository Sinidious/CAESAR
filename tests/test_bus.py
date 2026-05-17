from __future__ import annotations

import asyncio

import pytest

from caesar.bus.client import Bus, NotConnectedError


async def test_connect_idempotent(bus: Bus) -> None:
    # Already connected by the fixture; calling again should be a no-op.
    await bus.connect()
    assert bus.is_connected


async def test_close_idempotent(nats_url: str) -> None:
    b = Bus(nats_url)
    await b.connect()
    await b.close()
    # Second close should not raise.
    await b.close()
    assert not b.is_connected


async def test_publish_request_round_trip(bus: Bus) -> None:
    from nats.aio.msg import Msg

    received: list[bytes] = []

    async def handler(msg: Msg) -> None:
        received.append(msg.data)
        await msg.respond(b"pong")

    await bus.subscribe("test.ping", handler)
    reply = await bus.request("test.ping", b"hello", timeout=2.0)
    assert reply == b"pong"
    assert received == [b"hello"]


async def test_publish_without_subscriber_is_fire_and_forget(bus: Bus) -> None:
    await bus.publish("test.lonely", b"who-am-i-talking-to")
    # No assertion: just verifying it doesn't raise.


async def test_request_times_out_without_subscriber(bus: Bus) -> None:
    from nats.errors import NoRespondersError
    from nats.errors import TimeoutError as NATSTimeoutError

    with pytest.raises((NATSTimeoutError, NoRespondersError, asyncio.TimeoutError)):
        await bus.request("test.nobody", b"x", timeout=0.2)


async def test_methods_raise_when_not_connected(nats_url: str) -> None:
    from nats.aio.msg import Msg

    async def _noop(_msg: Msg) -> None:
        return None

    b = Bus(nats_url)
    with pytest.raises(NotConnectedError):
        await b.publish("any", b"")
    with pytest.raises(NotConnectedError):
        await b.subscribe("any", _noop)
    with pytest.raises(NotConnectedError):
        await b.request("any", b"")


async def test_raw_returns_underlying_client(bus: Bus) -> None:
    raw = bus.raw
    assert raw.is_connected


async def test_url_and_default_state() -> None:
    """Bus before connect has no client and reports the URL."""

    b = Bus("nats://example.invalid:4222")
    assert b.url == "nats://example.invalid:4222"
    assert not b.is_connected
