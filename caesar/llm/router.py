"""Task-routing wrapper for the LLM Gateway (ADR-0026).

A :class:`TaskRouter` holds one :class:`LLMGateway` per configured
task name plus a default. Calls to :meth:`complete` accept an optional
``task`` keyword; the router looks up the matching gateway and falls
back to the default when no override is configured.

The router itself satisfies the :class:`LLMGateway` Protocol
structurally (same ``complete`` signature, just with an extra optional
``task`` kwarg) so existing call sites that don't care about routing
keep working unchanged. The brain graph passes ``task="chat"`` when
it makes the LLM call; future workers can use their own task names.
"""

from __future__ import annotations

from caesar.llm.gateway import (
    ChatMessage,
    ChatResponse,
    LLMGateway,
    ToolDefinition,
)


class TaskRouter:
    """Dispatches ``complete`` to a per-task gateway with a fallback."""

    def __init__(
        self,
        default: LLMGateway,
        *,
        per_task: dict[str, LLMGateway] | None = None,
    ) -> None:
        self._default = default
        self._per_task: dict[str, LLMGateway] = dict(per_task or {})

    @property
    def default(self) -> LLMGateway:
        return self._default

    @property
    def routes(self) -> dict[str, LLMGateway]:
        """A read-only-ish view of the per-task overrides."""

        return dict(self._per_task)

    def gateway_for(self, task: str | None) -> LLMGateway:
        """Return the gateway that handles ``task``; default if unset."""

        if task is None:
            return self._default
        return self._per_task.get(task, self._default)

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
        task: str | None = None,
    ) -> ChatResponse:
        gateway = self.gateway_for(task)
        return await gateway.complete(
            messages,
            system=system,
            model=model,
            max_tokens=max_tokens,
            tools=tools,
        )
