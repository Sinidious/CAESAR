"""Tests for the v1.8 personal-facts store (ADR-0033)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.db.schema import audit_log, personal_facts
from caesar.memory.facts import Fact, FactsStore


@pytest.fixture
async def audit(engine: AsyncEngine) -> AsyncIterator[AuditLogger]:
    yield AuditLogger(engine, max_string_chars=4096)


@pytest.fixture
async def store(engine: AsyncEngine, audit: AuditLogger) -> AsyncIterator[FactsStore]:
    yield FactsStore(engine, audit)


async def _audit_events(engine: AsyncEngine, event_type: str) -> list[dict[str, Any]]:
    async with engine.begin() as conn:
        result = await conn.execute(
            select(audit_log.c.event_type, audit_log.c.payload).where(
                audit_log.c.event_type == event_type
            )
        )
        return [{"event_type": r.event_type, "payload": r.payload} for r in result]


async def _row_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(select(personal_facts.c.id))
        return len(list(result))


# --- Schema (migration applied via the engine fixture) -------------------


async def test_personal_facts_table_exists(engine: AsyncEngine) -> None:
    """The Alembic migration created the personal_facts table."""

    async with engine.connect() as conn:
        result = await conn.execute(select(personal_facts.c.id))
        assert list(result) == []  # empty but queryable


# --- get / list_all ------------------------------------------------------


async def test_get_missing_returns_none(store: FactsStore) -> None:
    assert await store.get("dog.name") is None


async def test_list_all_empty(store: FactsStore) -> None:
    assert await store.list_all() == []


async def test_list_all_orders_by_last_confirmed_desc(store: FactsStore) -> None:
    await store.upsert(key="dog.name", value="Beans")
    await store.upsert(key="spouse.name", value="Alice")
    rows = await store.list_all()
    assert [f.key for f in rows] == ["spouse.name", "dog.name"]


# --- upsert: insert ------------------------------------------------------


async def test_upsert_inserts_new_fact_with_audit(store: FactsStore, engine: AsyncEngine) -> None:
    fact = await store.upsert(
        key="dog.name",
        value="Beans",
        confidence=0.95,
        source_audit_id=42,
    )
    assert isinstance(fact, Fact)
    assert fact.key == "dog.name"
    assert fact.value == "Beans"
    assert fact.confidence == 0.95
    assert fact.source_audit_id == 42
    assert fact.first_seen_at == fact.last_confirmed_at

    added = await _audit_events(engine, "memory.fact.added")
    assert len(added) == 1
    assert added[0]["payload"]["key"] == "dog.name"
    assert added[0]["payload"]["value"] == "Beans"
    assert added[0]["payload"]["confidence"] == 0.95
    assert added[0]["payload"]["source_audit_id"] == 42


async def test_upsert_defaults_confidence_to_one(store: FactsStore) -> None:
    fact = await store.upsert(key="dog.name", value="Beans")
    assert fact.confidence == 1.0


# --- upsert: same value re-arrives (confirm) ----------------------------


async def test_upsert_same_value_emits_confirmed_not_updated(
    store: FactsStore, engine: AsyncEngine
) -> None:
    first = await store.upsert(key="dog.name", value="Beans", confidence=0.7)
    second = await store.upsert(key="dog.name", value="Beans", confidence=0.9)

    assert first.id == second.id
    # Confidence ratchets up (max).
    assert second.confidence == 0.9
    assert second.last_confirmed_at >= first.last_confirmed_at

    confirmed = await _audit_events(engine, "memory.fact.confirmed")
    assert len(confirmed) == 1
    assert confirmed[0]["payload"]["key"] == "dog.name"
    assert confirmed[0]["payload"]["confidence"] == 0.9
    # No "updated" row — the value didn't change.
    assert await _audit_events(engine, "memory.fact.updated") == []


async def test_upsert_same_value_does_not_lower_confidence(
    store: FactsStore,
) -> None:
    """Confidence is a ratchet: a noisy second observation doesn't
    lower the store's confidence in a fact."""

    await store.upsert(key="dog.name", value="Beans", confidence=0.95)
    fact = await store.upsert(key="dog.name", value="Beans", confidence=0.2)
    assert fact.confidence == 0.95


# --- upsert: different value (update) -----------------------------------


async def test_upsert_different_value_overwrites_and_audits_change(
    store: FactsStore, engine: AsyncEngine
) -> None:
    await store.upsert(key="dog.name", value="Beans", confidence=0.8)
    updated = await store.upsert(key="dog.name", value="Bowser", confidence=0.95)

    assert updated.value == "Bowser"
    assert updated.confidence == 0.95
    # Only one row in the table — UNIQUE(key) prevents duplicates.
    assert await _row_count(engine) == 1

    rows = await _audit_events(engine, "memory.fact.updated")
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["key"] == "dog.name"
    assert payload["old_value"] == "Beans"
    assert payload["new_value"] == "Bowser"


# --- delete --------------------------------------------------------------


async def test_delete_existing_returns_true_and_audits(
    store: FactsStore, engine: AsyncEngine
) -> None:
    await store.upsert(key="dog.name", value="Beans")
    removed = await store.delete("dog.name")
    assert removed is True
    assert await store.get("dog.name") is None

    rows = await _audit_events(engine, "memory.fact.deleted")
    assert len(rows) == 1
    assert rows[0]["payload"]["key"] == "dog.name"
    assert rows[0]["payload"]["value"] == "Beans"
    assert rows[0]["payload"]["reason"] == "operator"


async def test_delete_missing_returns_false_no_audit(
    store: FactsStore, engine: AsyncEngine
) -> None:
    removed = await store.delete("nonexistent")
    assert removed is False
    assert await _audit_events(engine, "memory.fact.deleted") == []


async def test_delete_with_custom_reason(store: FactsStore, engine: AsyncEngine) -> None:
    await store.upsert(key="dog.name", value="Beans")
    await store.delete("dog.name", reason="system")
    rows = await _audit_events(engine, "memory.fact.deleted")
    assert rows[0]["payload"]["reason"] == "system"


# --- user_edit / user_delete (dashboard surface) ------------------------


async def test_user_edit_emits_user_edited_audit(store: FactsStore, engine: AsyncEngine) -> None:
    await store.upsert(key="dog.name", value="Beans")
    await store.user_edit(key="dog.name", value="Bowser")
    # Both the underlying update audit AND the user_edited overlay
    # land — operators can filter the timeline by either.
    assert len(await _audit_events(engine, "memory.fact.user_edited")) == 1
    assert len(await _audit_events(engine, "memory.fact.updated")) == 1


async def test_user_edit_creates_new_fact_when_key_unknown(
    store: FactsStore,
) -> None:
    """Operator can hand-create a fact via the dashboard."""

    fact = await store.user_edit(key="dog.name", value="Beans")
    assert fact.value == "Beans"
    assert await store.get("dog.name") is not None


async def test_user_delete_existing_returns_true_and_audits(
    store: FactsStore, engine: AsyncEngine
) -> None:
    await store.upsert(key="dog.name", value="Beans")
    removed = await store.user_delete("dog.name")
    assert removed is True
    rows = await _audit_events(engine, "memory.fact.user_deleted")
    assert len(rows) == 1
    assert rows[0]["payload"]["key"] == "dog.name"
    assert rows[0]["payload"]["value"] == "Beans"
    # The plain "memory.fact.deleted" row is NOT emitted — only the
    # user-flavoured one — so the dashboard timeline is unambiguous.
    assert await _audit_events(engine, "memory.fact.deleted") == []


async def test_user_delete_missing_returns_false(store: FactsStore) -> None:
    assert await store.user_delete("nonexistent") is False
