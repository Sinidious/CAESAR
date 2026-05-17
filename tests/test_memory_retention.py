from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.db.schema import audit_log
from caesar.memory.retention import RetentionSweeper, sweep_once


async def _insert(engine: AsyncEngine, ts: datetime, event_type: str = "x") -> None:
    async with engine.begin() as conn:
        await conn.execute(insert(audit_log).values(ts=ts, event_type=event_type, payload={"k": 1}))


async def _count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(select(func.count()).select_from(audit_log))).scalar_one())


async def test_sweep_deletes_only_old_rows(engine: AsyncEngine) -> None:
    now = datetime.now(UTC)
    await _insert(engine, now - timedelta(days=120))  # old
    await _insert(engine, now - timedelta(days=10))  # young
    audit = AuditLogger(engine)

    result = await sweep_once(engine, retention_days=90, audit=audit)

    assert result.deleted == 1
    assert result.dry_run is False
    # 1 young row + 1 sweep-audit row = 2 total.
    assert await _count(engine) == 2


async def test_sweep_dry_run_does_not_delete(engine: AsyncEngine) -> None:
    now = datetime.now(UTC)
    await _insert(engine, now - timedelta(days=200))
    await _insert(engine, now - timedelta(days=200))
    audit = AuditLogger(engine)

    result = await sweep_once(engine, retention_days=90, audit=audit, dry_run=True)

    assert result.deleted == 2
    assert result.dry_run is True
    assert await _count(engine) == 2  # no row removed, no audit written


async def test_sweep_with_nothing_to_delete_writes_no_audit_row(
    engine: AsyncEngine,
) -> None:
    audit = AuditLogger(engine)
    result = await sweep_once(engine, retention_days=90, audit=audit)
    assert result.deleted == 0
    assert await _count(engine) == 0


async def test_sweep_rejects_zero_retention(engine: AsyncEngine) -> None:
    with pytest.raises(ValueError, match=">= 1"):
        await sweep_once(engine, retention_days=0)


async def test_sweep_without_audit_does_not_record(engine: AsyncEngine) -> None:
    """When audit is None, no audit row is written even after a real delete."""

    await _insert(engine, datetime.now(UTC) - timedelta(days=400))
    result = await sweep_once(engine, retention_days=90, audit=None)
    assert result.deleted == 1
    assert await _count(engine) == 0


async def test_start_stop_background_lifecycle(engine: AsyncEngine) -> None:
    """Start/stop are both idempotent and track the task handle."""

    audit = AuditLogger(engine)
    sweeper = RetentionSweeper(engine, audit, retention_days=30, interval_seconds=60)

    assert sweeper._task is None
    sweeper.start_background()
    sweeper.start_background()  # idempotent
    assert sweeper._task is not None

    await sweeper.stop_background()
    await sweeper.stop_background()  # idempotent
    assert sweeper._task is None


async def test_background_sweep_eventually_runs(engine: AsyncEngine) -> None:
    """Given enough time, the background loop performs at least one sweep
    that deletes old rows and writes an audit entry."""

    audit = AuditLogger(engine)
    await _insert(engine, datetime.now(UTC) - timedelta(days=200))

    sweeper = RetentionSweeper(engine, audit, retention_days=30, interval_seconds=0.05)
    sweeper.start_background()
    try:
        for _ in range(100):
            async with engine.connect() as conn:
                rows = (await conn.execute(select(audit_log))).all()
            if any(r.event_type == "memory.retention_sweep" for r in rows):
                return
            await asyncio.sleep(0.05)
        raise AssertionError("background sweep did not write an audit row")
    finally:
        await sweeper.stop_background()


async def test_background_loop_keeps_running_on_failure(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sweep that raises must not kill the loop."""

    audit = AuditLogger(engine)
    sweeper = RetentionSweeper(engine, audit, retention_days=30, interval_seconds=0.05)

    calls = 0
    real_sweep = sweeper.sweep

    async def flaky() -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return await real_sweep()

    monkeypatch.setattr(sweeper, "sweep", flaky)
    sweeper.start_background()
    try:
        for _ in range(100):
            if calls >= 2:
                break
            await asyncio.sleep(0.02)
        assert calls >= 2
    finally:
        await sweeper.stop_background()


async def test_sweeper_properties_expose_settings(engine: AsyncEngine) -> None:
    audit = AuditLogger(engine)
    sweeper = RetentionSweeper(engine, audit, retention_days=14, interval_seconds=60.0)
    assert sweeper.retention_days == 14
    assert sweeper.interval_seconds == 60.0
