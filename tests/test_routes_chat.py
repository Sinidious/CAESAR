from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.schema import audit_log


async def test_chat_returns_reply_and_audits(client: AsyncClient, engine: AsyncEngine):
    r = await client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["message"]["role"] == "assistant"
    assert body["message"]["content"] == "hello back"
    assert body["model"] == "fake-model"
    assert body["usage"] == {"input_tokens": 1, "output_tokens": 2}
    assert body["audit_log_id"] >= 1
    assert len(body["decision_id"]) == 32

    async with engine.connect() as conn:
        rows = (await conn.execute(select(audit_log))).all()
    assert len(rows) == 1
    only = rows[0]
    assert only.event_type == "chat.completed"
    assert only.payload["decision_id"] == body["decision_id"]
    assert only.payload["reply"] == "hello back"
    assert only.payload["messages"][0]["content"] == "hello"


async def test_chat_rejects_empty_messages(client: AsyncClient):
    r = await client.post("/v1/chat", json={"messages": []})
    assert r.status_code == 422


async def test_chat_uses_request_model_when_supplied(client: AsyncClient):
    r = await client.post(
        "/v1/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "model": "claude-other",
        },
    )
    assert r.status_code == 200
    # FakeGateway echoes the requested model in its response.
    assert r.json()["model"] == "claude-other"


async def test_chat_uses_overridden_system_prompt(client, fake_gateway, engine):
    """When the settings_store has a system prompt, chat uses it."""

    from caesar.db.settings_store import SettingsStore

    store = SettingsStore(engine)
    await store.set_system_prompt("Custom voice from the dashboard.")
    r = await client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    # The first gateway call should see the overridden prompt.
    assert fake_gateway.calls[0]["system"] == "Custom voice from the dashboard."
