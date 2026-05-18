"""Tests for the v1.8 memory.extract worker (ADR-0033)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.db.schema import audit_log, memory_extract_cursor
from caesar.llm.gateway import ChatMessage, ChatResponse, ToolDefinition
from caesar.memory.extract import (
    MEMORY_EXTRACT_TASK,
    FactCandidate,
    MemoryExtractor,
    _format_chat_for_extraction,
    _parse_facts,
)
from caesar.memory.facts import FactsStore


class _ScriptedGateway:
    """LLMGateway stand-in: returns canned responses, records every call."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.fail: Exception | None = None

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
        task: str | None = None,
    ) -> ChatResponse:
        self.calls.append(
            {
                "messages": messages,
                "system": system,
                "model": model,
                "max_tokens": max_tokens,
                "tools": tools,
                "task": task,
            }
        )
        if self.fail is not None:
            raise self.fail
        content = self._replies.pop(0) if self._replies else "[]"
        return ChatResponse(
            content=content,
            model="fake-extractor",
            input_tokens=10,
            output_tokens=5,
            tool_uses=[],
        )


@pytest.fixture
async def audit(engine: AsyncEngine) -> AsyncIterator[AuditLogger]:
    yield AuditLogger(engine, max_string_chars=4096)


@pytest.fixture
async def store(engine: AsyncEngine, audit: AuditLogger) -> AsyncIterator[FactsStore]:
    yield FactsStore(engine, audit)


async def _insert_chat_completed(
    engine: AsyncEngine,
    *,
    messages: list[dict[str, str]] | None = None,
    reply: str = "ack",
) -> int:
    """Insert a synthetic chat.completed audit row; returns its id."""

    from datetime import UTC, datetime

    async with engine.begin() as conn:
        result = await conn.execute(
            insert(audit_log).values(
                ts=datetime.now(UTC),
                event_type="chat.completed",
                payload={
                    "decision_id": "test",
                    "model": "fake-model",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "messages": messages or [{"role": "user", "content": "hi"}],
                    "reply": reply,
                    "iterations": 1,
                },
            )
        )
        return int(result.inserted_primary_key[0])  # type: ignore[index]


# --- _parse_facts ---------------------------------------------------------


def test_parse_facts_empty_array() -> None:
    assert _parse_facts("[]") == []


def test_parse_facts_well_formed() -> None:
    raw = '[{"key": "dog.name", "value": "Beans", "confidence": 0.9}]'
    out = _parse_facts(raw)
    assert out == [FactCandidate(key="dog.name", value="Beans", confidence=0.9)]


def test_parse_facts_strips_markdown_fences() -> None:
    """Some models wrap output in ```json fences despite instructions."""

    raw = '```json\n[{"key":"x.y","value":"z"}]\n```'
    out = _parse_facts(raw)
    assert out == [FactCandidate(key="x.y", value="z", confidence=1.0)]


def test_parse_facts_skips_malformed_entries() -> None:
    """An invalid entry shouldn't kill the whole batch."""

    raw = """[
        {"key": "dog.name", "value": "Beans"},
        {"key": "no_value"},
        "not an object",
        {"value": "no key"},
        {"key": "", "value": "empty key"},
        {"key": "ok", "value": "ok"}
    ]"""
    out = _parse_facts(raw)
    assert [c.key for c in out] == ["dog.name", "ok"]


def test_parse_facts_clamps_confidence_range() -> None:
    raw = '[{"key": "k", "value": "v", "confidence": 1.5}]'
    out = _parse_facts(raw)
    assert out[0].confidence == 1.0
    raw = '[{"key": "k", "value": "v", "confidence": -0.5}]'
    out = _parse_facts(raw)
    assert out[0].confidence == 0.0


def test_parse_facts_defaults_confidence_when_missing() -> None:
    raw = '[{"key": "k", "value": "v"}]'
    out = _parse_facts(raw)
    assert out[0].confidence == 1.0


def test_parse_facts_defaults_confidence_when_unparseable() -> None:
    raw = '[{"key": "k", "value": "v", "confidence": "kinda sure"}]'
    out = _parse_facts(raw)
    assert out[0].confidence == 1.0


def test_parse_facts_invalid_json_returns_empty() -> None:
    """Bad JSON shouldn't crash — return empty so cursor advances."""

    assert _parse_facts("not json at all") == []


def test_parse_facts_non_list_root_returns_empty() -> None:
    assert _parse_facts('{"key": "x", "value": "y"}') == []


def test_parse_facts_empty_string_returns_empty() -> None:
    assert _parse_facts("") == []
    assert _parse_facts("   ") == []


# --- _format_chat_for_extraction ----------------------------------------


def test_format_chat_includes_operator_messages_and_reply() -> None:
    payload = {
        "messages": [{"role": "user", "content": "my dog's name is Beans"}],
        "reply": "Nice to meet Beans!",
    }
    out = _format_chat_for_extraction(payload)
    assert out is not None
    assert "Beans" in out
    assert "Operator:" in out
    assert "CAESAR:" in out


def test_format_chat_skips_when_messages_missing() -> None:
    assert _format_chat_for_extraction({"reply": "ack"}) is None


def test_format_chat_skips_when_reply_missing() -> None:
    assert _format_chat_for_extraction({"messages": [{"role": "user", "content": "hi"}]}) is None


def test_format_chat_skips_when_no_user_messages() -> None:
    """Non-user messages alone shouldn't trigger extraction."""

    payload = {
        "messages": [{"role": "assistant", "content": "hi"}],
        "reply": "ack",
    }
    assert _format_chat_for_extraction(payload) is None


# --- MemoryExtractor: cursor + batch fetch ------------------------------


async def test_cursor_starts_at_zero(engine: AsyncEngine, store: FactsStore) -> None:
    extractor = MemoryExtractor(engine, _ScriptedGateway([]), store)
    assert await extractor._read_cursor() == 0


async def test_cursor_writes_and_persists(engine: AsyncEngine, store: FactsStore) -> None:
    extractor = MemoryExtractor(engine, _ScriptedGateway([]), store)
    await extractor._write_cursor(42)
    assert await extractor._read_cursor() == 42
    # Update path: second write replaces, doesn't append.
    await extractor._write_cursor(99)
    assert await extractor._read_cursor() == 99
    async with engine.connect() as conn:
        rows = list(await conn.execute(select(memory_extract_cursor)))
    assert len(rows) == 1


async def test_extract_once_empty_when_no_pending_rows(
    engine: AsyncEngine, store: FactsStore
) -> None:
    extractor = MemoryExtractor(engine, _ScriptedGateway([]), store)
    result = await extractor.extract_once()
    assert result.rows_processed == 0
    assert result.facts_added == 0


# --- MemoryExtractor: end-to-end ----------------------------------------


async def test_extract_once_writes_facts_from_llm_output(
    engine: AsyncEngine, store: FactsStore
) -> None:
    await _insert_chat_completed(
        engine,
        messages=[{"role": "user", "content": "my dog Beans loves walks"}],
        reply="Got it.",
    )
    gateway = _ScriptedGateway(['[{"key": "dog.name", "value": "Beans"}]'])
    extractor = MemoryExtractor(engine, gateway, store)
    result = await extractor.extract_once()
    assert result.rows_processed == 1
    assert result.facts_added == 1
    fact = await store.get("dog.name")
    assert fact is not None
    assert fact.value == "Beans"
    # Gateway was invoked with the right task name (operator can route).
    assert gateway.calls[0]["task"] == MEMORY_EXTRACT_TASK


async def test_extract_once_advances_cursor_past_processed_rows(
    engine: AsyncEngine, store: FactsStore
) -> None:
    id1 = await _insert_chat_completed(engine)
    id2 = await _insert_chat_completed(engine)
    extractor = MemoryExtractor(engine, _ScriptedGateway(["[]", "[]"]), store)
    await extractor.extract_once()
    assert await extractor._read_cursor() == id2
    # Second call: cursor is past both rows, nothing to process.
    result = await extractor.extract_once()
    assert result.rows_processed == 0
    del id1  # silence unused


async def test_extract_once_counts_added_vs_confirmed_vs_updated(
    engine: AsyncEngine, store: FactsStore
) -> None:
    """Fresh insert → added; same value re-seen → confirmed; new value → updated."""

    # Pre-seed a fact so the second insert hits the "same value" path.
    await store.upsert(key="dog.name", value="Beans", confidence=0.5)

    await _insert_chat_completed(engine, reply="r1")
    await _insert_chat_completed(engine, reply="r2")
    await _insert_chat_completed(engine, reply="r3")
    gateway = _ScriptedGateway(
        [
            '[{"key": "dog.name", "value": "Beans"}]',  # confirmed
            '[{"key": "dog.name", "value": "Bowser"}]',  # updated
            '[{"key": "spouse.name", "value": "Alice"}]',  # added
        ]
    )
    extractor = MemoryExtractor(engine, gateway, store)
    result = await extractor.extract_once()
    assert result.rows_processed == 3
    assert result.facts_confirmed == 1
    assert result.facts_updated == 1
    assert result.facts_added == 1


async def test_extract_once_skips_unparseable_llm_output_but_advances_cursor(
    engine: AsyncEngine, store: FactsStore
) -> None:
    """Bad output ⇒ no fact, but the cursor still moves so we don't loop."""

    audit_id = await _insert_chat_completed(engine)
    extractor = MemoryExtractor(
        engine,
        _ScriptedGateway(["this is not JSON at all"]),
        store,
    )
    result = await extractor.extract_once()
    assert result.rows_processed == 1
    assert result.facts_added == 0
    assert await extractor._read_cursor() == audit_id


async def test_extract_once_skips_rows_with_unusable_payload(
    engine: AsyncEngine, store: FactsStore
) -> None:
    """A chat.completed row without messages still advances the cursor —
    we don't loop on garbage we can't extract from."""

    from datetime import UTC, datetime

    async with engine.begin() as conn:
        result = await conn.execute(
            insert(audit_log).values(
                ts=datetime.now(UTC),
                event_type="chat.completed",
                payload={"reply": "no messages key"},  # malformed
            )
        )
        audit_id = int(result.inserted_primary_key[0])  # type: ignore[index]

    gateway = _ScriptedGateway([])
    extractor = MemoryExtractor(engine, gateway, store)
    out = await extractor.extract_once()
    assert out.rows_processed == 1
    assert out.facts_added == 0
    # Gateway was never called — we skipped before the LLM.
    assert gateway.calls == []
    assert await extractor._read_cursor() == audit_id


async def test_extract_once_handles_llm_failure_without_aborting(
    engine: AsyncEngine, store: FactsStore
) -> None:
    """LLM provider hiccup ⇒ no facts, but the cursor still moves so a
    persistent failure doesn't lock the queue."""

    audit_id = await _insert_chat_completed(engine)
    gateway = _ScriptedGateway([])
    gateway.fail = RuntimeError("provider down")
    extractor = MemoryExtractor(engine, gateway, store)
    result = await extractor.extract_once()
    assert result.rows_processed == 1
    assert result.facts_added == 0
    assert await extractor._read_cursor() == audit_id


# --- background lifecycle -----------------------------------------------


async def test_start_and_stop_background_lifecycle(engine: AsyncEngine, store: FactsStore) -> None:
    extractor = MemoryExtractor(
        engine,
        _ScriptedGateway([]),
        store,
        interval_seconds=0.01,
    )
    assert bool(extractor.is_running) is False
    extractor.start_background()
    await asyncio.sleep(0.05)
    assert bool(extractor.is_running) is True
    await extractor.stop_background()
    assert bool(extractor.is_running) is False
    # Idempotent stop.
    await extractor.stop_background()


async def test_start_background_is_idempotent(engine: AsyncEngine, store: FactsStore) -> None:
    """Calling start_background twice doesn't spawn two loops."""

    extractor = MemoryExtractor(
        engine,
        _ScriptedGateway([]),
        store,
        interval_seconds=0.01,
    )
    extractor.start_background()
    first_task = extractor._task
    extractor.start_background()
    second_task = extractor._task
    assert first_task is second_task
    await extractor.stop_background()


async def test_background_loop_swallows_errors(engine: AsyncEngine, store: FactsStore) -> None:
    """A row that throws inside extract_once shouldn't kill the loop."""

    class _Boomer(MemoryExtractor):
        async def extract_once(self):
            raise RuntimeError("inner boom")

    extractor = _Boomer(
        engine,
        _ScriptedGateway([]),
        store,
        interval_seconds=0.01,
    )
    extractor.start_background()
    await asyncio.sleep(0.05)
    # Task still running despite the inner exception.
    assert extractor.is_running
    await extractor.stop_background()
