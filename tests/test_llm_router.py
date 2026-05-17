"""Tests for the per-task LLM router (ADR-0026)."""

from __future__ import annotations

from typing import Any

from caesar.llm.gateway import ChatMessage, ChatResponse, ToolDefinition
from caesar.llm.router import TaskRouter


class _RecordingGateway:
    """Minimal LLMGateway implementation that records each call.

    Returns a deterministic response so tests can compare which
    gateway answered.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(
            {
                "messages": messages,
                "system": system,
                "model": model,
                "max_tokens": max_tokens,
                "tools": tools,
                "task": task,
            }
        )
        return ChatResponse(
            content=f"from {self.name}",
            model=model or self.name,
            input_tokens=1,
            output_tokens=2,
        )


def _msg() -> list[ChatMessage]:
    return [ChatMessage(role="user", content="hi")]


async def test_router_uses_default_when_no_task_kwarg() -> None:
    default = _RecordingGateway("default")
    router = TaskRouter(default=default)

    resp = await router.complete(_msg())
    assert resp.content == "from default"
    assert len(default.calls) == 1


async def test_router_uses_default_when_task_not_routed() -> None:
    default = _RecordingGateway("default")
    other = _RecordingGateway("other")
    router = TaskRouter(default=default, per_task={"recall_summary": other})

    resp = await router.complete(_msg(), task="chat")
    assert resp.content == "from default"
    assert len(default.calls) == 1
    assert len(other.calls) == 0


async def test_router_dispatches_to_per_task_gateway() -> None:
    default = _RecordingGateway("default")
    chat_gw = _RecordingGateway("chat")
    router = TaskRouter(default=default, per_task={"chat": chat_gw})

    resp = await router.complete(_msg(), task="chat")
    assert resp.content == "from chat"
    assert len(chat_gw.calls) == 1
    assert len(default.calls) == 0


async def test_router_passes_through_other_kwargs() -> None:
    default = _RecordingGateway("default")
    router = TaskRouter(default=default)

    await router.complete(
        _msg(),
        system="be terse",
        model="gpt-4o-mini",
        max_tokens=64,
        task="chat",
    )

    call = default.calls[0]
    assert call["system"] == "be terse"
    assert call["model"] == "gpt-4o-mini"
    assert call["max_tokens"] == 64


def test_gateway_for_returns_default_for_none() -> None:
    default = _RecordingGateway("default")
    router = TaskRouter(default=default)
    assert router.gateway_for(None) is default


def test_gateway_for_returns_default_for_unconfigured_task() -> None:
    default = _RecordingGateway("default")
    router = TaskRouter(default=default, per_task={"chat": _RecordingGateway("chat")})
    assert router.gateway_for("recall") is default


def test_routes_property_is_independent_copy() -> None:
    """Mutating the returned dict must not affect the router."""

    default = _RecordingGateway("default")
    chat_gw = _RecordingGateway("chat")
    router = TaskRouter(default=default, per_task={"chat": chat_gw})
    snapshot = router.routes
    snapshot.clear()
    assert router.gateway_for("chat") is chat_gw
