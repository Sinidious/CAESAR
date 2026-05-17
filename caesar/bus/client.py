"""NATS client wrapper (ADR-0009).

Single owner of the connection lifecycle. Callers ``await
bus.connect()`` at startup and ``await bus.close()`` at shutdown.
Between, they can ``publish``, ``request``, and ``subscribe``.

We never expose the underlying ``nats-py`` Client. If we ever swap
buses (extremely unlikely; ADR-0009 commits us to NATS), only this
module changes.

When constructed with auth (:class:`BusAuth`, per ADR-0027) the
connection is authenticated via NKEY signing. The seed material
itself is held off-host where it can — only the seed-callback
closure keeps it in process memory long enough to sign the
challenge.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
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


class BusAuthError(BusError):
    """Auth was requested but the seed couldn't be loaded."""


@dataclass(frozen=True)
class BusAuth:
    """Resolved NKEY auth material for :class:`Bus` (ADR-0027).

    Either ``nkey_seed`` (inline) or ``nkey_seed_path`` (file) must
    be set when ``Bus`` is constructed with auth. ``user`` is
    optional and only used when the operator's ``nats-server.conf``
    pairs the NKEY with a named user.
    """

    nkey_seed: str | None = None
    nkey_seed_path: Path | None = None
    user: str | None = None

    def resolve_seed(self) -> bytes:
        """Return the raw seed bytes that sign the NATS challenge."""

        if self.nkey_seed is not None:
            return self.nkey_seed.encode("utf-8")
        if self.nkey_seed_path is not None:
            try:
                return self.nkey_seed_path.read_bytes().strip()
            except OSError as exc:
                raise BusAuthError(
                    f"failed to read NKEY seed from {self.nkey_seed_path}: {exc}"
                ) from exc
        raise BusAuthError(
            "BusAuth requires either nkey_seed or nkey_seed_path to be set.",
        )


class Bus:
    """Async NATS connection wrapper."""

    def __init__(
        self,
        url: str,
        *,
        connect_timeout: float = 5.0,
        auth: BusAuth | None = None,
    ) -> None:
        self._url = url
        self._connect_timeout = connect_timeout
        self._auth = auth
        self._nc: NATSClient | None = None
        self._logger = get_logger("caesar.bus")

    @property
    def url(self) -> str:
        return self._url

    @property
    def is_connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected

    @property
    def authenticated(self) -> bool:
        """True when this bus was constructed with credentials."""

        return self._auth is not None

    def _client(self) -> NATSClient:
        if self._nc is None:
            raise NotConnectedError("Bus.connect() has not been called.")
        return self._nc

    def _connect_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"connect_timeout": self._connect_timeout}
        if self._auth is None:
            return kwargs
        seed = self._auth.resolve_seed()  # validates early, raises BusAuthError
        # nats-py accepts the seed as either a file path (``nkeys_seed``)
        # or an inline string (``nkeys_seed_str``); we always normalise
        # to the inline string form because we already resolved the file
        # ourselves and want a single signing path the tests can exercise.
        kwargs["nkeys_seed_str"] = seed.decode("utf-8")
        if self._auth.user is not None:
            kwargs["user"] = self._auth.user
        return kwargs

    async def connect(self) -> None:
        """Open the connection. Idempotent."""

        if self._nc is not None:
            return
        self._nc = await nats.connect(self._url, **self._connect_kwargs())
        self._logger.info(
            "bus.connected",
            url=self._url,
            authenticated=self.authenticated,
        )

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

    @property
    def raw(self) -> Any:
        """Escape hatch for code that genuinely needs the SDK object."""

        return self._client()
