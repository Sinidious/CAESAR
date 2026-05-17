"""Server-side worker registry (ADR-0009).

Subscribes to ``legion.registry.register`` and remembers who's
available. Exposes a synchronous lookup (``find``) and an async
dispatch (``dispatch``) that picks a worker for a capability,
publishes a :class:`TaskDispatch`, and returns the
:class:`TaskResult`.

Selection is round-robin per capability — simplest fair policy.
Worker health / liveness checking is a later concern; for v0.3 a
worker is "alive" if it registered during this process's lifetime.
"""

from __future__ import annotations

import itertools
import uuid
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

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


class NoWorkerAvailableError(RuntimeError):
    """No registered worker advertises the requested capability."""


class WorkerRegistry:
    """Tracks registered workers and routes dispatches to them."""

    def __init__(self, bus: Bus, *, default_timeout: float = 5.0) -> None:
        self._bus = bus
        self._default_timeout = default_timeout
        self._workers: dict[str, WorkerRegistration] = {}
        self._rr: dict[str, itertools.cycle[str]] = {}
        self._sub: Subscription | None = None
        self._logger = get_logger("caesar.legion.registry")

    @property
    def workers(self) -> dict[str, WorkerRegistration]:
        return dict(self._workers)

    async def start(self) -> None:
        """Subscribe to the registration subject."""

        if self._sub is not None:
            return
        self._sub = await self._bus.subscribe(REGISTRATION_SUBJECT, self._on_register)
        self._logger.info("registry.started")

    async def stop(self) -> None:
        if self._sub is not None:
            await self._sub.unsubscribe()
            self._sub = None
            self._logger.info("registry.stopped")

    async def _on_register(self, msg: Msg) -> None:
        try:
            reg = WorkerRegistration.model_validate_json(msg.data)
        except ValueError as exc:
            self._logger.warning("registry.bad_registration", error=str(exc))
            return
        self._workers[reg.worker_id] = reg
        self._rebuild_rr()
        self._logger.info(
            "registry.registered",
            worker_id=reg.worker_id,
            capabilities=reg.capabilities,
            version=reg.version,
        )

    def _rebuild_rr(self) -> None:
        per_cap: dict[str, list[str]] = defaultdict(list)
        for w in self._workers.values():
            for cap in w.capabilities:
                per_cap[cap].append(w.worker_id)
        self._rr = {cap: itertools.cycle(ids) for cap, ids in per_cap.items()}

    def find(self, capability: str) -> list[WorkerRegistration]:
        """Return every worker advertising ``capability`` (snapshot)."""

        return [w for w in self._workers.values() if capability in w.capabilities]

    def _pick(self, capability: str) -> str:
        rr = self._rr.get(capability)
        if rr is None:
            raise NoWorkerAvailableError(f"no worker registered with capability {capability!r}")
        return next(rr)

    async def dispatch(
        self,
        capability: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> TaskResult:
        """Send one task to a worker and await its reply."""

        worker_id = self._pick(capability)
        task = TaskDispatch(
            task_id=uuid.uuid4().hex,
            capability=capability,
            payload=payload or {},
        )
        self._logger.info(
            "registry.dispatch",
            task_id=task.task_id,
            capability=capability,
            worker_id=worker_id,
        )
        data = await self._bus.request(
            dispatch_subject(worker_id),
            task.model_dump_json().encode(),
            timeout=timeout if timeout is not None else self._default_timeout,
        )
        result = TaskResult.model_validate_json(data)
        self._logger.info(
            "registry.result",
            task_id=task.task_id,
            worker_id=worker_id,
            success=result.success,
        )
        return result

    def capabilities(self) -> Iterable[str]:
        return self._rr.keys()
