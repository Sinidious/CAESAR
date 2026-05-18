"""Proactive brain entry (ADR-0030).

Glue between the :class:`Scheduler` and the existing brain graph. The
scheduler fires :class:`Trigger`\\s; :class:`ProactiveRunner.fire`
turns each one into a brain invocation with:

- A ``decision_id`` prefixed ``proactive-<trigger_id>-<rand>`` so audit
  rows from this run are trivially greppable in the dashboard.
- A system prompt composed with ``proactive=True`` so the safety
  preamble biases the LLM toward *summarise + notify* over
  *act on the house* — operators who explicitly want a scheduled run
  to flip switches must still allow those services in ``policy.yaml``.
- The operator's current system-prompt override (same hot-swap path as
  ``/v1/chat`` reads).

The runner intentionally builds a fresh graph per fire. Graphs are
cheap (a LangGraph compile over our 2-node state machine) and this
keeps the runner stateless — useful if v1.6 ever wants to dispatch
proactive fires across multiple Praetor instances.
"""

from __future__ import annotations

import uuid

from caesar.db.audit import AuditLogger
from caesar.db.settings_store import SettingsStore
from caesar.ha.client import HAClient
from caesar.legion.registry import WorkerRegistry
from caesar.llm.gateway import ChatMessage, LLMGateway
from caesar.log import get_logger
from caesar.policy.engine import Policy
from caesar.proactive.triggers import Trigger

logger = get_logger("caesar.proactive.runner")


class ProactiveRunner:
    """Runs proactive triggers through the brain graph."""

    def __init__(
        self,
        *,
        gateway: LLMGateway,
        ha: HAClient | None,
        policy: Policy,
        audit: AuditLogger,
        registry: WorkerRegistry | None,
        settings_store: SettingsStore,
        default_model: str,
        default_prompt: str,
    ) -> None:
        self._gateway = gateway
        self._ha = ha
        self._policy = policy
        self._audit = audit
        self._registry = registry
        self._settings_store = settings_store
        self._default_model = default_model
        self._default_prompt = default_prompt

    async def fire(self, trigger: Trigger) -> None:
        """Run ``trigger.prompt`` through the brain graph.

        The Scheduler is responsible for ``trigger.fired`` /
        ``trigger.completed`` / ``trigger.error`` audit rows; this
        method only invokes the graph and lets tool-level audit rows
        (``tool.called`` etc.) propagate from there.
        """

        # Local imports break a module-load cycle: caesar.praetor.app
        # imports this module at top-level, and caesar.praetor.graph
        # transitively imports caesar.praetor.app via the package
        # __init__. Deferring the import to call time avoids it.
        from caesar.praetor.graph import build_brain_graph
        from caesar.praetor.safety import compose_system_prompt

        decision_id = f"proactive-{trigger.id}-{uuid.uuid4().hex[:12]}"
        overridden = await self._settings_store.get_system_prompt()
        effective_prompt = overridden or self._default_prompt
        system = compose_system_prompt(effective_prompt, proactive=True)
        graph = build_brain_graph(
            gateway=self._gateway,
            ha=self._ha,
            policy=self._policy,
            audit=self._audit,
            registry=self._registry,
        )
        logger.info(
            "proactive.fire",
            trigger_id=trigger.id,
            decision_id=decision_id,
            model=self._default_model,
        )
        await graph.ainvoke(
            {
                "messages": [ChatMessage(role="user", content=trigger.prompt)],
                "system": system,
                "model": self._default_model,
                "decision_id": decision_id,
                "iteration": 0,
            }
        )
