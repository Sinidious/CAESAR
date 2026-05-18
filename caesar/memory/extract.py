"""Personal-fact extractor (ADR-0033, v1.8).

Polls recent ``chat.completed`` audit rows, asks an LLM what durable
facts the operator revealed, and writes the validated results via
:class:`caesar.memory.facts.FactsStore`. Mirrors the
:class:`caesar.memory.semantic.SemanticIndexer` lifecycle so operators
recognise the shape (start_background / stop_background / poll).

Three design pieces from ADR-0033 §2 land here:

- **Cursor**: a singleton ``memory_extract_cursor`` row tracks the
  highest ``audit_log.id`` already processed. A Praetor restart picks
  up where it left off; we never re-extract the same conversation
  twice.
- **Task-routed gateway**: extraction goes through
  ``gateway.complete(..., task="memory_extract")`` so operators can
  route it to a cheap local Ollama model (per ADR-0026).
- **JSON-only output contract**: the system prompt asks for a JSON
  array; we tolerate empty arrays (the common case), parse with
  ``json.loads``, and skip rows that emit unparseable output without
  blocking the cursor advance.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.schema import audit_log, memory_extract_cursor
from caesar.llm.gateway import ChatMessage, LLMGateway
from caesar.log import get_logger
from caesar.memory.facts import FactsStore

logger = get_logger("caesar.memory.extract")

MEMORY_EXTRACT_TASK = "memory_extract"

EXTRACTION_SYSTEM_PROMPT = """\
You are CAESAR's fact extractor. Read the operator's conversation
with CAESAR below and return durable personal facts the operator
revealed about themselves, their household, preferences, or
environment.

Return ONLY a JSON array. No prose, no markdown, no commentary.
Schema:

[
  {"key": "dog.name", "value": "Beans", "confidence": 0.95},
  {"key": "preference.coffee", "value": "black, no sugar", "confidence": 0.7}
]

Rules:
- Keys are dot-namespaced snake_case (dog.name, preference.coffee,
  address.city, schedule.work_hours, family.spouse_name).
- Value is short: a name, a phrase, a single sentence. Never a paragraph.
- Confidence in 0.0..1.0 reflects your certainty.
- Skip transient facts (today's weather, one-time meeting times).
- Skip the operator's questions to CAESAR — they're not facts about
  the operator.
- An empty array [] is the common case and the right answer when
  nothing durable was said. Return that, not prose explaining why.
"""


@dataclass(frozen=True, slots=True)
class FactCandidate:
    """One LLM-extracted fact before it lands in the store."""

    key: str
    value: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ExtractBatchResult:
    """Outcome of one :meth:`MemoryExtractor.extract_once` run."""

    rows_processed: int
    facts_added: int
    facts_updated: int
    facts_confirmed: int


def _format_chat_for_extraction(payload: dict[str, Any]) -> str | None:
    """Render a ``chat.completed`` payload as a readable transcript.

    Returns ``None`` if the row doesn't have enough structure to be
    worth extracting from.
    """

    messages = payload.get("messages")
    reply = payload.get("reply")
    if not isinstance(messages, list) or not isinstance(reply, str):
        return None

    lines: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role == "user" and isinstance(content, str) and content.strip():
            lines.append(f"Operator: {content.strip()}")
    if not lines:
        return None

    lines.append(f"CAESAR: {reply.strip()}")
    return "\n".join(lines)


def _parse_facts(raw: str) -> list[FactCandidate]:
    """Parse the extractor LLM's JSON-array output into validated candidates.

    Tolerant: skips malformed entries; rejects the whole batch only
    when the outer JSON parse fails (caller advances the cursor anyway
    — the row was unusable, no point looping on it).
    """

    if not raw or not raw.strip():
        return []
    text = raw.strip()
    # Some models wrap output in ```json fences despite instructions.
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        decoded = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(decoded, list):
        return []

    candidates: list[FactCandidate] = []
    for entry in decoded:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        value = entry.get("value")
        confidence = entry.get("confidence", 1.0)
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if not key.strip() or not value.strip():
            continue
        try:
            confidence_float = float(confidence)
        except (TypeError, ValueError):
            confidence_float = 1.0
        # Clamp to the documented [0.0, 1.0] range.
        confidence_float = max(0.0, min(1.0, confidence_float))
        candidates.append(
            FactCandidate(
                key=key.strip(),
                value=value.strip(),
                confidence=confidence_float,
            )
        )
    return candidates


class MemoryExtractor:
    """Background loop that turns ``chat.completed`` rows into facts."""

    def __init__(
        self,
        engine: AsyncEngine,
        gateway: LLMGateway,
        store: FactsStore,
        *,
        interval_seconds: float = 60.0,
        batch_size: int = 8,
    ) -> None:
        self._engine = engine
        self._gateway = gateway
        self._store = store
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # --- one-shot API used by the background loop and tests ---------

    async def _read_cursor(self) -> int:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(memory_extract_cursor))).first()
        if row is None:
            return 0
        return int(row.last_audit_id)

    async def _write_cursor(self, value: int) -> None:
        now = datetime.now(UTC)
        async with self._engine.begin() as conn:
            existing = (await conn.execute(select(memory_extract_cursor))).first()
            if existing is None:
                await conn.execute(
                    insert(memory_extract_cursor).values(
                        id=1,
                        last_audit_id=value,
                        updated_at=now,
                    )
                )
            else:
                await conn.execute(
                    update(memory_extract_cursor)
                    .where(memory_extract_cursor.c.id == existing.id)
                    .values(last_audit_id=value, updated_at=now)
                )

    async def _fetch_batch(self, after_id: int) -> list[dict[str, Any]]:
        stmt = (
            select(audit_log.c.id, audit_log.c.payload)
            .where(audit_log.c.event_type == "chat.completed")
            .where(audit_log.c.id > after_id)
            .order_by(audit_log.c.id)
            .limit(self._batch_size)
        )
        async with self._engine.connect() as conn:
            return [dict(r) for r in (await conn.execute(stmt)).mappings()]

    async def _extract_one(self, transcript: str) -> list[FactCandidate]:
        """Run one LLM extraction; tolerate any provider hiccup."""

        try:
            response = await self._gateway.complete(
                [ChatMessage(role="user", content=transcript)],
                system=EXTRACTION_SYSTEM_PROMPT,
                task=MEMORY_EXTRACT_TASK,
            )
        except Exception as exc:
            logger.warning(
                "memory.extract.llm_failed",
                error=type(exc).__name__,
                message=str(exc),
            )
            return []
        return _parse_facts(response.content)

    async def extract_once(self) -> ExtractBatchResult:
        """Process one batch. Idempotent given a fixed cursor + rows."""

        cursor = await self._read_cursor()
        rows = await self._fetch_batch(cursor)
        if not rows:
            return ExtractBatchResult(0, 0, 0, 0)

        added = updated = confirmed = 0
        max_id = cursor
        for row in rows:
            audit_id = int(row["id"])
            payload = row["payload"] or {}
            transcript = _format_chat_for_extraction(payload)
            if transcript is not None:
                candidates = await self._extract_one(transcript)
                for candidate in candidates:
                    existing = await self._store.get(candidate.key)
                    fact = await self._store.upsert(
                        key=candidate.key,
                        value=candidate.value,
                        confidence=candidate.confidence,
                        source_audit_id=audit_id,
                    )
                    if existing is None:
                        added += 1
                    elif existing.value == fact.value:
                        confirmed += 1
                    else:
                        updated += 1
            max_id = audit_id

        await self._write_cursor(max_id)
        logger.info(
            "memory.extract.batch",
            rows_processed=len(rows),
            facts_added=added,
            facts_updated=updated,
            facts_confirmed=confirmed,
            cursor_advanced_to=max_id,
        )
        return ExtractBatchResult(
            rows_processed=len(rows),
            facts_added=added,
            facts_updated=updated,
            facts_confirmed=confirmed,
        )

    # --- background lifecycle ---------------------------------------

    async def _loop(self) -> None:
        while True:
            try:
                await self.extract_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "memory.extract.loop_error",
                    error=type(exc).__name__,
                    message=str(exc),
                )
            try:
                await asyncio.sleep(self._interval_seconds)
            except asyncio.CancelledError:
                raise

    def start_background(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="memory.extract.loop")
        logger.info(
            "memory.extract.started",
            interval_seconds=self._interval_seconds,
            batch_size=self._batch_size,
        )

    async def stop_background(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("memory.extract.stopped")
