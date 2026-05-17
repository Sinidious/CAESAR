"""NATS client wrapper (ADR-0009).

Single owner of the connection lifecycle. Callers ``await
bus.connect()`` at startup and ``await bus.close()`` at shutdown.
Between, they can ``publish``, ``request``, and ``subscribe``.

We never expose the underlying ``nats-py`` Client. If we ever swap
buses (extremely unlikely; ADR-0009 commits us to NATS), only this
module changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import nats
from nats.aio.client import Client as NATSClient
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription

from caesar.log import get_logger

MessageHandler = Callable[[Msg], Awaitable[None]]


class BusError(RuntimeError):
    """Generic bus failure."""


class NotConnectedError(BusError):
    """The caller used the bus before calling :meth:`Bus.connect`."""


class Bus:
    """Async NATS connection wrapper."""

    def __init__(self, url: str, *, connect_timeout: float = 5.0) -> None:
        self._url = url
        self._connect_timeout = connect_timeout
        self._nc: NATSClient | None = None
        self._logger = get_logger("caesar.bus")

    @property
    def url(self) -> str:
        return self._url

    @property
    def is_connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected

    def _client(self) -> NATSClient:
        if self._nc is None:
            raise NotConnectedError("Bus.connect() has not been called.")
        return self._nc

    async def connect(self) -> None:
        """Open the connection. Idempotent."""

        if self._nc is not None:
            return
        self._nc = await nats.connect(
            self._url,
            connect_timeout=self._connect_timeout,
        )
        self._logger.info("bus.connected", url=self._url)

    async def close(self) -> None:
        """Drain and close the connection. Idempotent."""

        if self._nc is None:
            return
        await self._nc.drain()
        self._nc = None
        self._logger.info("bus.closed")

    async def publish(self, subject: str, payload: bytes) -> None:
        await self._client().publish(subject, payload)

    async def request(self, subject: str, payload: bytes, *, timeout: float = 5.0) -> bytes:
        msg = await self._client().request(subject, payload, timeout=timeout)
        return msg.data

    async def subscribe(self, subject: str, cb: MessageHandler) -> Subscription:
        return await self._client().subscribe(subject, cb=cb)

    async def reply(self, msg: Msg, payload: bytes) -> None:
        """Convenience wrapper around :meth:`Msg.respond`."""

        await msg.respond(payload)

    @property
    def raw(self) -> Any:
        """Escape hatch for code that genuinely needs the SDK object."""

        return self._client()
