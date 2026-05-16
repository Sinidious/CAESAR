from __future__ import annotations

from httpx import AsyncClient

from caesar import __version__


async def test_healthz_ok(client: AsyncClient):
    r = await client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "ok", "version": __version__}
