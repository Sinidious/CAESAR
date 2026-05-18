"""Personal-facts store (ADR-0033, v1.8).

CAESAR's operator-visible memory of "what you told me". One row per
fact, keyed by a dot-namespaced identifier (``dog.name``,
``preference.coffee``, ``address.city``). Facts update in place: when
the operator's value contradicts the stored one, ``value`` overwrites
and an audit row records the change.

The store is intentionally narrow:

- :class:`Fact` is the canonical row shape (read-only).
- :class:`FactsStore` exposes the three writers
  (:meth:`upsert`, :meth:`confirm`, :meth:`delete`) plus the two
  readers (:meth:`get`, :meth:`list_all`).
- Every write emits one audit-log row so the change history is
  replayable in the dashboard / by `git blame`-style debugging.

The extraction worker (v1.8, ADR-0033 §2) is the canonical writer;
operators editing facts via the dashboard call the same methods so
operator overrides and machine extraction share one path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.db.schema import personal_facts
from caesar.log import get_logger

logger = get_logger("caesar.memory.facts")


@dataclass(frozen=True, slots=True)
class Fact:
    """One row of :data:`personal_facts`."""

    id: int
    key: str
    value: str
    confidence: float
    first_seen_at: datetime
    last_confirmed_at: datetime
    source_audit_id: int | None


def _now_utc() -> datetime:
    return datetime.now(UTC)


class FactsStore:
    """CRUD for :data:`personal_facts` with audit-row side effects."""

    def __init__(
        self,
        engine: AsyncEngine,
        audit: AuditLogger,
    ) -> None:
        self._engine = engine
        self._audit = audit

    # --- reads ----------------------------------------------------------

    async def get(self, key: str) -> Fact | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(personal_facts).where(personal_facts.c.key == key),
                )
            ).first()
        if row is None:
            return None
        return _row_to_fact(row)

    async def list_all(self) -> list[Fact]:
        """Return every fact, ordered by ``last_confirmed_at`` DESC.

        Retrieval auto-inject (ADR-0033 §3) consumes the head of this
        list — fresher facts win when the size cap is hit.
        """

        async with self._engine.connect() as conn:
            rows = await conn.execute(
                select(personal_facts).order_by(personal_facts.c.last_confirmed_at.desc()),
            )
            return [_row_to_fact(r) for r in rows]

    # --- writes ---------------------------------------------------------

    async def upsert(
        self,
        *,
        key: str,
        value: str,
        confidence: float = 1.0,
        source_audit_id: int | None = None,
    ) -> Fact:
        """Insert a new fact or update an existing one (UNIQUE(key)).

        Emits ``memory.fact.added`` when the key is new,
        ``memory.fact.updated`` when the value changed, or
        ``memory.fact.confirmed`` when the same value re-arrives.
        """

        now = _now_utc()
        existing = await self.get(key)
        if existing is None:
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    insert(personal_facts).values(
                        key=key,
                        value=value,
                        confidence=confidence,
                        first_seen_at=now,
                        last_confirmed_at=now,
                        source_audit_id=source_audit_id,
                    ),
                )
                inserted = result.inserted_primary_key
                if inserted is None:  # pragma: no cover - defensive
                    raise RuntimeError("personal_facts insert returned no id")
                new_id = int(inserted[0])
            await self._audit.record(
                "memory.fact.added",
                {
                    "key": key,
                    "value": value,
                    "confidence": confidence,
                    "source_audit_id": source_audit_id,
                },
            )
            return Fact(
                id=new_id,
                key=key,
                value=value,
                confidence=confidence,
                first_seen_at=now,
                last_confirmed_at=now,
                source_audit_id=source_audit_id,
            )

        if existing.value == value:
            # Same value re-arriving — bump last_confirmed_at and the
            # confidence ratchet, audit as confirmed (not updated).
            new_confidence = max(existing.confidence, confidence)
            async with self._engine.begin() as conn:
                await conn.execute(
                    update(personal_facts)
                    .where(personal_facts.c.id == existing.id)
                    .values(
                        confidence=new_confidence,
                        last_confirmed_at=now,
                        source_audit_id=source_audit_id,
                    ),
                )
            await self._audit.record(
                "memory.fact.confirmed",
                {
                    "key": key,
                    "value": value,
                    "confidence": new_confidence,
                    "source_audit_id": source_audit_id,
                },
            )
            return Fact(
                id=existing.id,
                key=key,
                value=value,
                confidence=new_confidence,
                first_seen_at=existing.first_seen_at,
                last_confirmed_at=now,
                source_audit_id=source_audit_id,
            )

        # Value differs — overwrite and audit the change with both
        # old and new values.
        async with self._engine.begin() as conn:
            await conn.execute(
                update(personal_facts)
                .where(personal_facts.c.id == existing.id)
                .values(
                    value=value,
                    confidence=confidence,
                    last_confirmed_at=now,
                    source_audit_id=source_audit_id,
                ),
            )
        await self._audit.record(
            "memory.fact.updated",
            {
                "key": key,
                "old_value": existing.value,
                "new_value": value,
                "confidence": confidence,
                "source_audit_id": source_audit_id,
            },
        )
        return Fact(
            id=existing.id,
            key=key,
            value=value,
            confidence=confidence,
            first_seen_at=existing.first_seen_at,
            last_confirmed_at=now,
            source_audit_id=source_audit_id,
        )

    async def delete(self, key: str, *, reason: str = "operator") -> bool:
        """Delete a fact by key. Returns ``True`` iff a row was removed.

        Emits ``memory.fact.deleted`` carrying the prior value and the
        deletion reason ("operator" for dashboard edits, "system" for
        extraction-side cleanups).
        """

        existing = await self.get(key)
        if existing is None:
            return False
        async with self._engine.begin() as conn:
            await conn.execute(
                delete(personal_facts).where(personal_facts.c.id == existing.id),
            )
        await self._audit.record(
            "memory.fact.deleted",
            {
                "key": key,
                "value": existing.value,
                "reason": reason,
            },
        )
        return True

    async def user_edit(
        self,
        *,
        key: str,
        value: str,
        confidence: float = 1.0,
    ) -> Fact:
        """Operator-driven write from the dashboard.

        Functionally identical to :meth:`upsert` but emits a distinct
        ``memory.fact.user_edited`` audit row so the dashboard can
        filter operator corrections from machine extraction.
        """

        fact = await self.upsert(key=key, value=value, confidence=confidence)
        await self._audit.record(
            "memory.fact.user_edited",
            {
                "key": key,
                "value": value,
                "confidence": confidence,
            },
        )
        return fact

    async def user_delete(self, key: str) -> bool:
        """Operator-driven delete from the dashboard."""

        existing = await self.get(key)
        if existing is None:
            return False
        async with self._engine.begin() as conn:
            await conn.execute(
                delete(personal_facts).where(personal_facts.c.id == existing.id),
            )
        await self._audit.record(
            "memory.fact.user_deleted",
            {
                "key": key,
                "value": existing.value,
            },
        )
        return True


def _row_to_fact(row: object) -> Fact:
    """Coerce a SQLAlchemy Row into the public :class:`Fact` shape."""

    # ``row`` is a SQLAlchemy Row; attribute access works against
    # column names directly.
    return Fact(
        id=int(row.id),  # type: ignore[attr-defined]
        key=str(row.key),  # type: ignore[attr-defined]
        value=str(row.value),  # type: ignore[attr-defined]
        confidence=float(row.confidence),  # type: ignore[attr-defined]
        first_seen_at=row.first_seen_at,  # type: ignore[attr-defined]
        last_confirmed_at=row.last_confirmed_at,  # type: ignore[attr-defined]
        source_audit_id=(
            int(row.source_audit_id)  # type: ignore[attr-defined]
            if row.source_audit_id is not None  # type: ignore[attr-defined]
            else None
        ),
    )
