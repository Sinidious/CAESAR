"""Mock Home Assistant servers for tests.

Two flavours:

- :func:`make_rest_app` returns a FastAPI app you can mount behind an
  ``ASGITransport`` and hand to :class:`caesar.ha.HAClient` via its
  injected ``http=`` argument.
- :class:`FakeHAWebSocket` is an async context manager that spins up a
  real ``websockets`` server on a random localhost port and yields its
  WS URL. It implements just enough of HA's protocol for our tests:
  auth handshake, ``subscribe_events`` ack, and on-demand event push.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import websockets
from fastapi import FastAPI, HTTPException, Request, status

VALID_TOKEN = "test-token-please-let-me-in"


def make_rest_app(
    states: dict[str, dict[str, Any]] | None = None,
    *,
    record: list[dict[str, Any]] | None = None,
    fail_states_with: int | None = None,
) -> FastAPI:
    """Build a FastAPI app that mimics the HA REST endpoints we use.

    ``states`` keys are entity_ids, values are HA-shaped state dicts.
    ``record`` (if supplied) receives every service-call request body.
    ``fail_states_with`` makes ``GET /api/states`` return that status.
    """

    app = FastAPI()
    db = dict(states or {})

    def _require_auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        if header != f"Bearer {VALID_TOKEN}":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no/bad token")

    @app.get("/api/states")
    async def list_states(request: Request) -> list[dict[str, Any]]:
        _require_auth(request)
        if fail_states_with is not None:
            raise HTTPException(fail_states_with, "boom")
        return list(db.values())

    @app.get("/api/states/{entity_id}")
    async def get_state(entity_id: str, request: Request) -> dict[str, Any]:
        _require_auth(request)
        if entity_id not in db:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such entity")
        return db[entity_id]

    @app.post("/api/services/{domain}/{service}")
    async def call_service(domain: str, service: str, request: Request) -> dict[str, Any]:
        _require_auth(request)
        body = await request.json()
        if record is not None:
            record.append({"domain": domain, "service": service, "body": body})
        return {"ok": True}

    return app


class FakeHAWebSocket:
    """Real ``websockets`` server that speaks just enough HA WS protocol."""

    def __init__(
        self,
        *,
        accept_token: str = VALID_TOKEN,
        events: list[dict[str, Any]] | None = None,
        wrong_hello: bool = False,
        fail_subscribe: bool = False,
        noise_messages: list[dict[str, Any]] | None = None,
        close_after_events: bool = False,
    ) -> None:
        self._accept_token = accept_token
        self._events = events or []
        self._wrong_hello = wrong_hello
        self._fail_subscribe = fail_subscribe
        self._noise = noise_messages or []
        self._close_after_events = close_after_events
        self._server: Any = None
        self._port: int = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self._port}/api/websocket"

    @property
    def http_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def __aenter__(self) -> FakeHAWebSocket:
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        sockets = self._server.sockets
        assert sockets is not None
        self._port = sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handler(self, ws: Any) -> None:
        if self._wrong_hello:
            await ws.send(json.dumps({"type": "garbage"}))
            return

        await ws.send(json.dumps({"type": "auth_required"}))
        msg = json.loads(await ws.recv())
        if msg.get("type") != "auth" or msg.get("access_token") != self._accept_token:
            await ws.send(json.dumps({"type": "auth_invalid", "message": "bad"}))
            return
        await ws.send(json.dumps({"type": "auth_ok"}))

        sub = json.loads(await ws.recv())
        sub_id = sub.get("id", 1)
        if self._fail_subscribe:
            await ws.send(
                json.dumps({"id": sub_id, "type": "result", "success": False, "error": "no"})
            )
            return
        await ws.send(json.dumps({"id": sub_id, "type": "result", "success": True}))

        for noise in self._noise:
            await ws.send(json.dumps({"id": sub_id, **noise}))
        for event in self._events:
            await ws.send(json.dumps({"id": sub_id, "type": "event", "event": event}))

        if self._close_after_events:
            return

        # Otherwise hold the connection open until the client disconnects.
        with contextlib.suppress(websockets.ConnectionClosed):
            async for _ in ws:
                pass


@contextlib.asynccontextmanager
async def fake_ha_ws(**kwargs: Any) -> AsyncIterator[FakeHAWebSocket]:
    async with FakeHAWebSocket(**kwargs) as fake:
        # Tiny grace period so the server's accept loop is ready.
        await asyncio.sleep(0)
        yield fake
