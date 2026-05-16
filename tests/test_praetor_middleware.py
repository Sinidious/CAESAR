from __future__ import annotations

import re

from httpx import AsyncClient


async def test_request_id_is_minted_when_missing(client: AsyncClient):
    r = await client.get("/healthz")
    assert r.status_code == 200
    rid = r.headers.get("X-Request-Id")
    assert rid is not None
    assert re.fullmatch(r"[0-9a-f]{32}", rid)


async def test_request_id_is_echoed_when_supplied(client: AsyncClient):
    r = await client.get("/healthz", headers={"X-Request-Id": "req-abc"})
    assert r.headers["X-Request-Id"] == "req-abc"
