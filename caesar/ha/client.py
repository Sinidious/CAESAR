"""Home Assistant REST + WebSocket client (ADR-0007).

The REST client handles one-shot operations (list states, call
service). The WebSocket client subscribes to live events. Both share
the same long-lived access token; both are async; both are owned by a
single :class:`HAClient` instance so callers don't manage two
lifecycles.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import websockets
from websockets.asyncio.client import ClientConnection

from caesar.ha.models import EntityState, ServiceCall
from caesar.log import get_logger


class HAError(RuntimeError):
    """Generic HA-bridge failure."""


class HAAuthError(HAError):
    """HA rejected our access token."""


class HAClient:
    """Async HA client owning both REST and WS transports."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: float = 10.0,
        verify_ssl: bool = True,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._http = http or httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            verify=verify_ssl,
        )
        self._ws_msg_id = 0
        self._logger = get_logger("caesar.ha")

    @property
    def base_url(self) -> str:
        return self._base_url

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- REST ---------------------------------------------------------

    async def list_states(self) -> list[EntityState]:
        """Return all entity states known to HA."""

        resp = await self._http.get("/api/states")
        resp.raise_for_status()
        return [EntityState.model_validate(item) for item in resp.json()]

    async def get_state(self, entity_id: str) -> EntityState | None:
        """Return one entity's state, or ``None`` if it doesn't exist."""

        resp = await self._http.get(f"/api/states/{entity_id}")
        if resp.status_code == httpx.codes.NOT_FOUND:
            return None
        resp.raise_for_status()
        return EntityState.model_validate(resp.json())

    async def call_service(self, call: ServiceCall) -> None:
        """Invoke one HA service call."""

        body: dict[str, Any] = dict(call.data or {})
        if call.target:
            body["target"] = call.target
        resp = await self._http.post(
            f"/api/services/{call.domain}/{call.service}",
            json=body,
        )
        resp.raise_for_status()
        self._logger.info(
            "ha.service.called",
            domain=call.domain,
            service=call.service,
            target=call.target,
        )

    # --- WebSocket ----------------------------------------------------

    def _ws_url(self) -> str:
        return (
            self._base_url.replace("https://", "wss://").replace("http://", "ws://")
            + "/api/websocket"
        )

    async def _authenticate(self, ws: ClientConnection) -> None:
        hello = json.loads(await ws.recv())
        if hello.get("type") != "auth_required":
            raise HAError(f"unexpected hello: {hello!r}")
        await ws.send(json.dumps({"type": "auth", "access_token": self._token}))
        result = json.loads(await ws.recv())
        if result.get("type") != "auth_ok":
            raise HAAuthError(f"auth rejected: {result!r}")

    def _next_msg_id(self) -> int:
        self._ws_msg_id += 1
        return self._ws_msg_id

    async def subscribe_events(
        self, event_type: str | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield events from HA's WebSocket event stream.

        The connection lives for the duration of the iteration; close
        the iterator to disconnect.
        """

        async with websockets.connect(self._ws_url()) as ws:
            await self._authenticate(ws)

            sub_id = self._next_msg_id()
            sub_msg: dict[str, Any] = {"id": sub_id, "type": "subscribe_events"}
            if event_type:
                sub_msg["event_type"] = event_type
            await ws.send(json.dumps(sub_msg))

            ack = json.loads(await ws.recv())
            if not ack.get("success"):
                raise HAError(f"subscribe_events failed: {ack!r}")
            self._logger.info("ha.ws.subscribed", event_type=event_type)

            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "event":
                    yield msg["event"]
