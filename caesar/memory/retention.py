"""Time-based TTL sweep for episodic memory (ADR-0020).

A :class:`RetentionSweeper` deletes ``audit_log`` rows older than its
configured TTL. Praetor starts one at lifespan and lets it loop in the
background; operators can also run :func:`sweep_once` from the CLI
(``caesar memory sweep --apply``) for explicit maintenance.

Each successful sweep writes one audit row labelled
``memory.retention_sweep`` recording the cutoff and the row count.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.db.schema import audit_log
from caesar.log import get_logger


@dataclass(frozen=True)
class SweepResult:
    """Outcome of one sweep pass."""

    cutoff: datetime
    deleted: int
    dry_run: bool


async def sweep_once(
    engine: AsyncEngine,
    *,
    retention_days: int,
    dry_run: bool = False,
    audit: AuditLogger | None = None,
) -> SweepResult:
    """Delete (or count) audit rows older than ``retention_days`` days.

    When ``audit`` is supplied and not a dry-run, also writes a
    ``memory.retention_sweep`` row recording the outcome.
    """

    if retention_days < 1:
        raise ValueError("retention_days must be >= 1")
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    if dry_run:
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    select(func.count()).select_from(audit_log).where(audit_log.c.ts < cutoff)
                )
            ).scalar_one()
        return SweepResult(cutoff=cutoff, deleted=int(count), dry_run=True)

    async with engine.begin() as conn:
        result = await conn.execute(delete(audit_log).where(audit_log.c.ts < cutoff))
    deleted = result.rowcount or 0

    if audit is not None and deleted > 0:
        await audit.record(
            "memory.retention_sweep",
            {
                "cutoff": cutoff.isoformat(),
                "deleted": deleted,
                "retention_days": retention_days,
            },
        )
    return SweepResult(cutoff=cutoff, deleted=deleted, dry_run=False)


class RetentionSweeper:
    """Owns the background sweep loop for the lifespan of one Praetor."""

    def __init__(
        self,
        engine: AsyncEngine,
        audit: AuditLogger,
        *,
        retention_days: int,
        interval_seconds: float,
    ) -> None:
        self._engine = engine
        self._audit = audit
        self._retention_days = retention_days
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._logger = get_logger("caesar.memory.retention")

    @property
    def retention_days(self) -> int:
        return self._retention_days

    @property
    def interval_seconds(self) -> float:
        return self._interval_seconds

    @property
    def is_running(self) -> bool:
        """Whether the background loop is currently scheduled."""

        return self._task is not None

    async def sweep(self) -> SweepResult:
        """Run one sweep pass synchronously."""

        return await sweep_once(
            self._engine,
            retention_days=self._retention_days,
            audit=self._audit,
        )

    async def _loop(self) -> None:
        while True:
            try:
                result = await self.sweep()
                self._logger.info(
                    "memory.sweep.done",
                    deleted=result.deleted,
                    cutoff=result.cutoff.isoformat(),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning("memory.sweep.failed", error=str(exc))
            try:
                await asyncio.sleep(self._interval_seconds)
            except asyncio.CancelledError:
                raise

    def start_background(self) -> None:
        """Spawn the sweep loop. Idempotent."""

        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="memory.retention.sweep")
        self._logger.info(
            "memory.sweep.started",
            retention_days=self._retention_days,
            interval_seconds=self._interval_seconds,
        )

    async def stop_background(self) -> None:
        """Cancel the loop and await its exit. Idempotent."""

        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._logger.info("memory.sweep.stopped")
