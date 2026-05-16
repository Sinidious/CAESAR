from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.db.schema import audit_log


async def test_audit_record_writes_row(engine: AsyncEngine):
    audit = AuditLogger(engine)
    payload = {"decision_id": "d-1", "model": "fake"}
    row_id = await audit.record("test.event", payload)
    assert row_id >= 1

    async with engine.connect() as conn:
        rows = (await conn.execute(select(audit_log))).all()
    assert len(rows) == 1
    only = rows[0]
    assert only.event_type == "test.event"
    assert only.payload == payload
    assert only.ts is not None


async def test_audit_ids_are_monotonic(engine: AsyncEngine):
    audit = AuditLogger(engine)
    ids = [await audit.record("e", {"i": i}) for i in range(3)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 3
