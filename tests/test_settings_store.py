from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.settings_store import LLM_SYSTEM_PROMPT, SettingsStore


async def test_get_missing_returns_none(engine: AsyncEngine) -> None:
    store = SettingsStore(engine)
    assert await store.get("unset") is None
    assert await store.get_system_prompt() is None


async def test_set_then_get_roundtrip(engine: AsyncEngine) -> None:
    store = SettingsStore(engine)
    await store.set_system_prompt("You are CAESAR, the homelab voice.")
    assert await store.get_system_prompt() == "You are CAESAR, the homelab voice."


async def test_set_upserts_existing_key(engine: AsyncEngine) -> None:
    store = SettingsStore(engine)
    await store.set_system_prompt("v1")
    await store.set_system_prompt("v2")
    assert await store.get_system_prompt() == "v2"


async def test_generic_set_and_get(engine: AsyncEngine) -> None:
    store = SettingsStore(engine)
    await store.set("custom.key", "hello")
    assert await store.get("custom.key") == "hello"
    # Constant exposes the canonical key.
    assert LLM_SYSTEM_PROMPT == "llm.system_prompt"
