"""Unit tests for the grouping/query helpers behind dashboard views."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.schema import audit_log
from caesar.legion.protocol import WorkerRegistration
from caesar.praetor.dashboard.views import (
    group_intents,
    load_agent_activity,
    load_intents,
)


def _row(id_: int, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": id_,
        "ts": datetime.now(UTC),
        "event_type": event_type,
        "payload": payload,
    }


def test_group_intents_keys_by_chat_completed() -> None:
    rows = [
        _row(
            10,
            "chat.completed",
            {
                "decision_id": "d-A",
                "messages": [{"role": "user", "content": "lights"}],
                "reply": "ok",
            },
        ),
        _row(11, "service.called", {"decision_id": "d-A", "domain": "light"}),
        _row(
            12,
            "chat.completed",
            {
                "decision_id": "d-B",
                "messages": [{"role": "user", "content": "next"}],
                "reply": "sure",
            },
        ),
    ]
    intents = group_intents(rows, limit=5)
    assert {i.decision_id for i in intents} == {"d-A", "d-B"}
    a = next(i for i in intents if i.decision_id == "d-A")
    assert a.user_message == "lights"
    assert a.reply == "ok"
    # Both audit rows tied to d-A are in the trace.
    assert [e.event_type for e in a.events] == ["chat.completed", "service.called"]


def test_group_intents_skips_rows_without_decision_id() -> None:
    rows = [
        _row(1, "memory.retention_sweep", {"deleted": 7}),
        _row(
            2,
            "chat.completed",
            {
                "decision_id": "d-x",
                "messages": [{"role": "user", "content": "x"}],
                "reply": "y",
            },
        ),
    ]
    intents = group_intents(rows, limit=5)
    assert len(intents) == 1
    assert intents[0].decision_id == "d-x"


def test_group_intents_respects_limit() -> None:
    rows = []
    for i in range(5):
        rows.append(
            _row(
                100 + i,
                "chat.completed",
                {
                    "decision_id": f"d-{i}",
                    "messages": [{"role": "user", "content": str(i)}],
                    "reply": "",
                },
            )
        )
    intents = group_intents(rows, limit=2)
    assert len(intents) == 2


def test_group_intents_handles_missing_messages() -> None:
    """Edge case: chat.completed without a user message in the list."""

    rows = [_row(1, "chat.completed", {"decision_id": "d", "reply": "hi"})]
    intents = group_intents(rows, limit=5)
    assert intents[0].user_message == ""


def test_group_intents_handles_messages_with_no_user_role() -> None:
    rows = [
        _row(
            1,
            "chat.completed",
            {
                "decision_id": "d",
                "messages": [{"role": "assistant", "content": "weird"}],
                "reply": "ok",
            },
        )
    ]
    intents = group_intents(rows, limit=5)
    assert intents[0].user_message == ""


# --- DB-backed helpers -------------------------------------------------------


async def test_load_intents_pulls_recent(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        for i in range(3):
            await conn.execute(
                insert(audit_log).values(
                    ts=datetime.now(UTC),
                    event_type="chat.completed",
                    payload={
                        "decision_id": f"dec-{i}",
                        "messages": [{"role": "user", "content": f"q{i}"}],
                        "reply": f"r{i}",
                    },
                )
            )
    intents = await load_intents(engine, limit=10)
    assert {i.decision_id for i in intents} == {"dec-0", "dec-1", "dec-2"}


async def test_load_agent_activity_returns_workers_and_dispatches(
    engine: AsyncEngine,
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            insert(audit_log).values(
                ts=datetime.now(UTC),
                event_type="legion.dispatched",
                payload={
                    "decision_id": "d-1",
                    "task_id": "t-1",
                    "capability": "memory.recall",
                    "worker_id": "memory_recall",
                    "success": True,
                    "error": None,
                },
            )
        )
        await conn.execute(
            insert(audit_log).values(
                ts=datetime.now(UTC),
                event_type="legion.dispatched",
                payload={
                    "decision_id": None,
                    "task_id": "t-2",
                    "capability": "test.boom",
                    "worker_id": "boom",
                    "success": False,
                    "error": "kapow",
                },
            )
        )

    workers = {
        "memory_recall": WorkerRegistration(
            worker_id="memory_recall",
            capabilities=["memory.recall"],
            version="0.1.3",
        ),
        "noop": WorkerRegistration(worker_id="noop", capabilities=["test.noop"], version="0.1.3"),
    }
    agents, dispatches = await load_agent_activity(engine, workers=workers, history_limit=20)

    assert [a.worker_id for a in agents] == ["memory_recall", "noop"]
    assert len(dispatches) == 2
    failed = next(d for d in dispatches if not d.success)
    assert failed.error == "kapow"
    assert failed.decision_id is None
    succeeded = next(d for d in dispatches if d.success)
    assert succeeded.decision_id == "d-1"
