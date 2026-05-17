"""Read/write the operator-tunable ``app_settings`` table.

Flat key/value with a typed helper layer. The store is intentionally
small — it's a config surface, not a general key/value DB. Anything
worth a structured schema gets its own table.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.schema import app_settings

LLM_SYSTEM_PROMPT = "llm.system_prompt"


class SettingsStore:
    """Persistent runtime settings backed by ``app_settings``."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get(self, key: str) -> str | None:
        stmt = select(app_settings.c.value).where(app_settings.c.key == key)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.first()
        return None if row is None else str(row[0])

    async def set(self, key: str, value: str) -> None:
        stmt = sqlite_insert(app_settings).values(
            key=key, value=value, updated_at=datetime.now(UTC)
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[app_settings.c.key],
            set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get_system_prompt(self) -> str | None:
        return await self.get(LLM_SYSTEM_PROMPT)

    async def set_system_prompt(self, prompt: str) -> None:
        await self.set(LLM_SYSTEM_PROMPT, prompt)
