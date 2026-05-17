"""Tests for the OpenAI provider (ADR-0026, v1.1)."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from caesar.llm.gateway import ChatMessage, ToolDefinition, ToolResult, ToolUse
from caesar.llm.openai import OpenAIProvider, _messages_to_openai


def _fake_message(
    *,
    content: str | None = "hi",
    tool_calls: list[Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _fake_response(
    *,
    text: str | None = "hi",
    model: str = "gpt-test",
    finish_reason: str = "stop",
    prompt_tokens: int = 5,
    completion_tokens: int = 7,
    reasoning_tokens: int | None = None,
    tool_calls: list[Any] | None = None,
) -> SimpleNamespace:
    details = (
        SimpleNamespace(reasoning_tokens=reasoning_tokens) if reasoning_tokens is not None else None
    )
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        completion_tokens_details=details,
    )
    choice = SimpleNamespace(
        message=_fake_message(content=text, tool_calls=tool_calls),
        finish_reason=finish_reason,
    )
    return SimpleNamespace(choices=[choice], model=model, usage=usage)


# --- complete() basic round-trip --------------------------------------------


async def test_complete_passes_args_and_maps_response(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIProvider(
        api_key="sk-test",
        default_model="gpt-default",
        default_max_tokens=512,
    )
    create = AsyncMock(return_value=_fake_response())
    monkeypatch.setattr(provider._client.chat.completions, "create", create)

    resp = await provider.complete(
        [ChatMessage(role="user", content="hello")],
        system="You are CAESAR.",
        model="gpt-override",
        max_tokens=128,
    )

    assert resp.content == "hi"
    assert resp.model == "gpt-test"
    assert resp.input_tokens == 5
    assert resp.output_tokens == 7
    assert resp.stop_reason == "end_turn"
    assert resp.tool_uses == []

    assert create.await_args is not None
    kwargs = create.await_args.kwargs
    assert kwargs["model"] == "gpt-override"
    assert kwargs["max_completion_tokens"] == 128
    # The system message is hoisted to a leading role="system" entry.
    assert kwargs["messages"][0] == {"role": "system", "content": "You are CAESAR."}
    assert kwargs["messages"][1] == {"role": "user", "content": "hello"}


async def test_complete_defaults_model_and_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIProvider(
        api_key="sk-test",
        default_model="gpt-default",
        default_max_tokens=256,
    )
    create = AsyncMock(return_value=_fake_response())
    monkeypatch.setattr(provider._client.chat.completions, "create", create)

    await provider.complete([ChatMessage(role="user", content="hi")])

    assert create.await_args is not None
    kwargs = create.await_args.kwargs
    assert kwargs["model"] == "gpt-default"
    assert kwargs["max_completion_tokens"] == 256


async def test_complete_propagates_base_url() -> None:
    """``base_url`` reaches the underlying client for Azure-OpenAI / vLLM use."""

    provider = OpenAIProvider(
        api_key="sk-test",
        default_model="gpt-default",
        base_url="https://openai.azure.example/openai/v1",
    )
    assert str(provider._client.base_url).startswith("https://openai.azure.example")


# --- tool calling -----------------------------------------------------------


async def test_complete_serialises_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIProvider(api_key="sk-test", default_model="gpt")
    create = AsyncMock(return_value=_fake_response())
    monkeypatch.setattr(provider._client.chat.completions, "create", create)

    tools = [
        ToolDefinition(
            name="call_service",
            description="invoke an HA service",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
    ]
    await provider.complete([ChatMessage(role="user", content="?")], tools=tools)

    assert create.await_args is not None
    kwargs = create.await_args.kwargs
    assert kwargs["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "call_service",
                "description": "invoke an HA service",
                "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
            },
        }
    ]


async def test_complete_parses_tool_use_response(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIProvider(api_key="sk-test", default_model="gpt")
    tool_call = SimpleNamespace(
        id="call_123",
        type="function",
        function=SimpleNamespace(
            name="call_service",
            arguments='{"domain": "light", "service": "turn_on"}',
        ),
    )
    create = AsyncMock(
        return_value=_fake_response(text=None, finish_reason="tool_calls", tool_calls=[tool_call])
    )
    monkeypatch.setattr(provider._client.chat.completions, "create", create)

    resp = await provider.complete([ChatMessage(role="user", content="turn on")])

    assert resp.stop_reason == "tool_use"
    assert len(resp.tool_uses) == 1
    use = resp.tool_uses[0]
    assert use.id == "call_123"
    assert use.name == "call_service"
    assert use.input == {"domain": "light", "service": "turn_on"}


async def test_complete_handles_malformed_tool_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bad JSON in arguments shouldn't crash; it surfaces as _raw."""

    provider = OpenAIProvider(api_key="sk-test", default_model="gpt")
    tool_call = SimpleNamespace(
        id="call_bad",
        type="function",
        function=SimpleNamespace(name="thing", arguments="not-json"),
    )
    create = AsyncMock(
        return_value=_fake_response(text=None, finish_reason="tool_calls", tool_calls=[tool_call])
    )
    monkeypatch.setattr(provider._client.chat.completions, "create", create)

    resp = await provider.complete([ChatMessage(role="user", content="?")])

    assert resp.tool_uses[0].input == {"_raw": "not-json"}


async def test_complete_reasoning_tokens_roll_into_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0026: reasoning tokens are added to output_tokens."""

    provider = OpenAIProvider(api_key="sk-test", default_model="gpt")
    create = AsyncMock(
        return_value=_fake_response(prompt_tokens=10, completion_tokens=3, reasoning_tokens=27)
    )
    monkeypatch.setattr(provider._client.chat.completions, "create", create)

    resp = await provider.complete([ChatMessage(role="user", content="hi")])

    assert resp.input_tokens == 10
    assert resp.output_tokens == 30  # 3 visible + 27 reasoning


# --- message translation (unit) ---------------------------------------------


def test_messages_to_openai_assistant_with_tool_calls() -> None:
    msg = ChatMessage(
        role="assistant",
        content="thinking aloud",
        tool_uses=[ToolUse(id="call_1", name="t", input={"k": "v"})],
    )
    out = _messages_to_openai([msg], system=None)
    assert out == [
        {
            "role": "assistant",
            "content": "thinking aloud",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "t", "arguments": '{"k": "v"}'},
                }
            ],
        }
    ]


def test_messages_to_openai_tool_results_become_role_tool() -> None:
    msg = ChatMessage(
        role="user",
        tool_results=[ToolResult(tool_use_id="call_1", content="ok", is_error=False)],
    )
    out = _messages_to_openai([msg], system=None)
    assert out == [{"role": "tool", "tool_call_id": "call_1", "content": "ok"}]


def test_messages_to_openai_tool_results_plus_user_text() -> None:
    msg = ChatMessage(
        role="user",
        content="now do this",
        tool_results=[ToolResult(tool_use_id="call_1", content="ok")],
    )
    out = _messages_to_openai([msg], system=None)
    assert out == [
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        {"role": "user", "content": "now do this"},
    ]


def test_messages_to_openai_inline_system_messages_are_hoisted() -> None:
    """Pre-existing role=system messages collapse with the system kwarg."""

    out = _messages_to_openai(
        [
            ChatMessage(role="system", content="extra invariant"),
            ChatMessage(role="user", content="hi"),
        ],
        system="primary system",
    )
    assert out[0] == {"role": "system", "content": "primary system\n\nextra invariant"}
    assert out[1] == {"role": "user", "content": "hi"}


# --- gated live test --------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("CAESAR_LLM__OPENAI__API_KEY"),
    reason="Live OpenAI integration test (set CAESAR_LLM__OPENAI__API_KEY to run).",
)
async def test_live_openai_round_trip() -> None:  # pragma: no cover - live
    """Optional integration test; only runs when an API key is in env."""

    provider = OpenAIProvider(
        api_key=os.environ["CAESAR_LLM__OPENAI__API_KEY"],
        default_model="gpt-4o-mini",
        default_max_tokens=64,
    )
    resp = await provider.complete(
        [ChatMessage(role="user", content="Say 'pong' and nothing else.")]
    )
    assert "pong" in resp.content.lower()
