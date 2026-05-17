"""Wire shapes for Legion ↔ Praetor communication (ADR-0009).

Subjects (per ADR-0009):

- ``legion.registry.register`` — worker → registry; one-shot
  registration message.
- ``legion.<worker_id>.dispatch`` — registry → worker; request/reply
  with a :class:`TaskDispatch` and the worker's :class:`TaskResult`.

We intentionally use plain JSON over NATS (rather than something like
protobuf) so non-Python workers can join later with a small client.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

REGISTRATION_SUBJECT = "legion.registry.register"


def dispatch_subject(worker_id: str) -> str:
    """Subject a worker subscribes to for its dispatches."""

    return f"legion.{worker_id}.dispatch"


class WorkerRegistration(BaseModel):
    """One-shot announcement sent by a worker on startup."""

    worker_id: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    version: str = Field(min_length=1)


class TaskDispatch(BaseModel):
    """A unit of work the registry hands to a worker."""

    task_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    """Worker's reply to a :class:`TaskDispatch`."""

    task_id: str
    worker_id: str
    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None
