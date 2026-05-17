"""Query + grouping helpers behind the dashboard views.

Kept in their own module so they're easy to unit-test without
exercising the FastAPI routes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.schema import audit_log


@dataclass(frozen=True)
class TimelineRow:
    id: int
    ts: str
    event_type: str
    payload_preview: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Intent:
    decision_id: str
    started_at: str
    user_message: str
    reply: str
    events: list[TimelineRow]


def _stringify(row: Mapping[str, Any]) -> TimelineRow:
    payload = row["payload"] or {}
    return TimelineRow(
        id=int(row["id"]),
        ts=row["ts"].isoformat() if row["ts"] is not None else "",
        event_type=row["event_type"],
        payload_preview=json.dumps(payload, default=str)[:300],
        payload=payload,
    )


def _user_message(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content", ""))
    return ""


def group_intents(rows: Sequence[Mapping[str, Any]], *, limit: int) -> list[Intent]:
    """Group recent audit rows into intents keyed by ``decision_id``.

    A row joins an intent if its payload carries ``decision_id``
    matching a ``chat.completed`` row. Anything without a decision_id
    is dropped from the timeline view.
    """

    groups: dict[str, list[TimelineRow]] = {}
    headers: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        payload = row["payload"] or {}
        decision_id = payload.get("decision_id")
        if not isinstance(decision_id, str):
            continue
        groups.setdefault(decision_id, []).append(_stringify(row))
        if row["event_type"] == "chat.completed" and decision_id not in headers:
            headers[decision_id] = row

    intents: list[Intent] = []
    for decision_id, header in headers.items():
        events = sorted(groups[decision_id], key=lambda r: r.id)
        payload = header["payload"] or {}
        intents.append(
            Intent(
                decision_id=decision_id,
                started_at=header["ts"].isoformat() if header["ts"] is not None else "",
                user_message=_user_message(payload),
                reply=str(payload.get("reply", "")),
                events=events,
            )
        )
    # Newest chat.completed first.
    intents.sort(key=lambda i: i.started_at, reverse=True)
    return intents[:limit]


async def load_intents(engine: AsyncEngine, *, limit: int) -> list[Intent]:
    """Pull recent rows from the DB and group them into intents.

    We overfetch by ``limit * 10`` so each intent has a reasonable
    chance of pulling its supporting events from the same window.
    """

    stmt = select(audit_log).order_by(desc(audit_log.c.id)).limit(max(limit * 10, 100))
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).mappings().all()
    return group_intents([dict(row) for row in rows], limit=limit)


@dataclass(frozen=True)
class AgentRow:
    worker_id: str
    capabilities: list[str]
    version: str


@dataclass(frozen=True)
class DispatchRow:
    audit_log_id: int
    ts: str
    worker_id: str
    capability: str
    task_id: str
    success: bool
    error: str | None
    decision_id: str | None


async def load_agent_activity(
    engine: AsyncEngine,
    *,
    workers: Mapping[str, Any],
    history_limit: int,
) -> tuple[list[AgentRow], list[DispatchRow]]:
    """Read live worker registrations + recent ``legion.dispatched`` rows."""

    agents = [
        AgentRow(
            worker_id=reg.worker_id,
            capabilities=list(reg.capabilities),
            version=reg.version,
        )
        for reg in workers.values()
    ]
    agents.sort(key=lambda a: a.worker_id)

    stmt = (
        select(audit_log)
        .where(audit_log.c.event_type == "legion.dispatched")
        .order_by(desc(audit_log.c.id))
        .limit(history_limit)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).mappings().all()

    dispatches: list[DispatchRow] = []
    for row in rows:
        payload = row["payload"] or {}
        decision_id = payload.get("decision_id")
        dispatches.append(
            DispatchRow(
                audit_log_id=int(row["id"]),
                ts=row["ts"].isoformat() if row["ts"] is not None else "",
                worker_id=str(payload.get("worker_id", "")),
                capability=str(payload.get("capability", "")),
                task_id=str(payload.get("task_id", "")),
                success=bool(payload.get("success", False)),
                error=payload.get("error"),
                decision_id=decision_id if isinstance(decision_id, str) else None,
            )
        )
    return agents, dispatches
