"""Base Legion worker (ADR-0009).

Concrete workers subclass :class:`Worker`, override :meth:`handle`,
and call :meth:`start`. Start does two things:

1. Subscribe to ``legion.<worker_id>.dispatch`` and route each
   incoming :class:`TaskDispatch` through :meth:`handle`.
2. Publish a one-shot :class:`WorkerRegistration` to the registry.

Exceptions inside :meth:`handle` are caught and reported back as a
failed :class:`TaskResult` so a buggy worker can't take down the bus.
"""

from __future__ import annotations

from typing import ClassVar

from nats.aio.msg import Msg
from nats.aio.subscription import Subscription

from caesar.bus.client import Bus
from caesar.legion.protocol import (
    REGISTRATION_SUBJECT,
    TaskDispatch,
    TaskResult,
    WorkerRegistration,
    dispatch_subject,
)
from caesar.log import get_logger


class Worker:
    """Base async Legion worker."""

    worker_id: ClassVar[str] = ""
    capabilities: ClassVar[list[str]] = []
    version: ClassVar[str] = "0.0.0"

    def __init__(self, bus: Bus) -> None:
        if not self.worker_id:
            raise ValueError(f"{type(self).__name__} must set a non-empty `worker_id`.")
        if not self.capabilities:
            raise ValueError(f"{type(self).__name__} must declare at least one capability.")
        self._bus = bus
        self._sub: Subscription | None = None
        self._logger = get_logger(f"caesar.legion.{self.worker_id}")

    async def handle(self, task: TaskDispatch) -> dict[str, object]:
        """Override in subclasses; return the ``result`` payload."""

        raise NotImplementedError

    async def _on_dispatch(self, msg: Msg) -> None:
        try:
            task = TaskDispatch.model_validate_json(msg.data)
        except ValueError as exc:
            result = TaskResult(
                task_id="",
                worker_id=self.worker_id,
                success=False,
                error=f"invalid dispatch payload: {exc}",
            )
            await msg.respond(result.model_dump_json().encode())
            return

        try:
            payload = await self.handle(task)
        except Exception as exc:
            self._logger.warning(
                "worker.handle.error",
                task_id=task.task_id,
                error=str(exc),
            )
            result = TaskResult(
                task_id=task.task_id,
                worker_id=self.worker_id,
                success=False,
                error=str(exc),
            )
        else:
            result = TaskResult(
                task_id=task.task_id,
                worker_id=self.worker_id,
                success=True,
                result=dict(payload),
            )

        await msg.respond(result.model_dump_json().encode())

    async def start(self) -> None:
        """Subscribe to dispatches and announce registration."""

        self._sub = await self._bus.subscribe(dispatch_subject(self.worker_id), self._on_dispatch)
        reg = WorkerRegistration(
            worker_id=self.worker_id,
            capabilities=self.capabilities,
            version=self.version,
        )
        await self._bus.publish(REGISTRATION_SUBJECT, reg.model_dump_json().encode())
        self._logger.info(
            "worker.started",
            capabilities=self.capabilities,
            version=self.version,
        )

    async def stop(self) -> None:
        if self._sub is not None:
            await self._sub.unsubscribe()
            self._sub = None
            self._logger.info("worker.stopped")


class NoopWorker(Worker):
    """Test/diagnostic worker that echoes its input."""

    worker_id: ClassVar[str] = "noop"
    capabilities: ClassVar[list[str]] = ["test.noop"]
    version: ClassVar[str] = "0.1.3"

    async def handle(self, task: TaskDispatch) -> dict[str, object]:
        return {"echo": task.payload, "capability": task.capability}
