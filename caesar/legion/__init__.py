"""Legion — the worker pool (ADR-0009).

Workers register over the bus, accept dispatches, and reply with
results. PR A ships the protocol + registry + a noop worker for
testing; PR B brings the first real worker (memory recall).
"""

from caesar.legion.memory_recall import MemoryRecallWorker
from caesar.legion.protocol import (
    REGISTRATION_SUBJECT,
    TaskDispatch,
    TaskResult,
    WorkerRegistration,
    dispatch_subject,
)
from caesar.legion.registry import NoWorkerAvailableError, WorkerRegistry
from caesar.legion.semantic_recall import SemanticRecallWorker
from caesar.legion.worker import NoopWorker, Worker

__all__ = [
    "REGISTRATION_SUBJECT",
    "MemoryRecallWorker",
    "NoWorkerAvailableError",
    "NoopWorker",
    "SemanticRecallWorker",
    "TaskDispatch",
    "TaskResult",
    "Worker",
    "WorkerRegistration",
    "WorkerRegistry",
    "dispatch_subject",
]
