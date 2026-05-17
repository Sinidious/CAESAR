"""Tests for the Ollama provider (ADR-0026, v1.1)."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock

import pytest

from caesar.llm.gateway import ChatMessage, ToolDefinition, ToolResult, ToolUse
from caesar.llm.ollama import OllamaProvider, _coerce_tool_arguments, _messages_to_ollama


def _ok_response(
    *,
    text: str = "hi",
    model: str = "llama3.1:8b-instruct",
    prompt_eval_count: int = 5,
    eval_count: int = 7,
    tool_calls: list[Any] | None = None,
    done_reason: str = "stop",
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "model": model,
        "message": message,
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
        "done_reason": done_reason,
    }


# --- complete() basic round-trip --------------------------------------------


async def test_complete_passes_args_and_maps_response(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(default_model="llama3.1:8b-instruct", default_max_tokens=512)
    chat = AsyncMock(return_value=_ok_response())
    monkeypatch.setattr(provider._client, "chat", chat)

    resp = await provider.complete(
        [ChatMessage(role="user", content="hello")],
        system="You are CAESAR.",
        model="qwen2.5",
        max_tokens=128,
    )

    assert resp.content == "hi"
    assert resp.model == "llama3.1:8b-instruct"
    assert resp.input_tokens == 5
    assert resp.output_tokens == 7
    assert resp.stop_reason == "end_turn"
    assert resp.tool_uses == []

    assert chat.await_args is not None
    kwargs = chat.await_args.kwargs
    assert kwargs["model"] == "qwen2.5"
    assert kwargs["options"] == {"num_predict": 128}
    assert kwargs["messages"][0] == {"role": "system", "content": "You are CAESAR."}
    assert kwargs["messages"][1] == {"role": "user", "content": "hello"}


async def test_complete_defaults_model_and_num_predict(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(default_model="llama3.1", default_max_tokens=256)
    chat = AsyncMock(return_value=_ok_response())
    monkeypatch.setattr(provider._client, "chat", chat)

    await provider.complete([ChatMessage(role="user", content="hi")])

    assert chat.await_args is not None
    kwargs = chat.await_args.kwargs
    assert kwargs["model"] == "llama3.1"
    assert kwargs["options"]["num_predict"] == 256


def test_provider_propagates_base_url() -> None:
    provider = OllamaProvider(
        default_model="llama3.1",
        base_url="http://gpu-box.lan:11434",
    )
    assert provider._base_url == "http://gpu-box.lan:11434"


async def test_complete_handles_done_reason_length(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(default_model="llama3.1")
    chat = AsyncMock(return_value=_ok_response(done_reason="length"))
    monkeypatch.setattr(provider._client, "chat", chat)

    resp = await provider.complete([ChatMessage(role="user", content="hi")])
    assert resp.stop_reason == "max_tokens"


# --- tool calling -----------------------------------------------------------


async def test_complete_serialises_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(default_model="llama3.1")
    chat = AsyncMock(return_value=_ok_response())
    monkeypatch.setattr(provider._client, "chat", chat)

    tools = [
        ToolDefinition(
            name="call_service",
            description="invoke an HA service",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
    ]
    await provider.complete([ChatMessage(role="user", content="?")], tools=tools)

    assert chat.await_args is not None
    assert chat.await_args.kwargs["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "call_service",
                "description": "invoke an HA service",
                "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
            },
        }
    ]


async def test_complete_parses_tool_use_with_dict_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ollama 0.4+ returns tool arguments as a dict."""

    provider = OllamaProvider(default_model="llama3.1")
    chat = AsyncMock(
        return_value=_ok_response(
            text="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "call_service",
                        "arguments": {"domain": "light", "service": "turn_on"},
                    },
                }
            ],
        )
    )
    monkeypatch.setattr(provider._client, "chat", chat)

    resp = await provider.complete([ChatMessage(role="user", content="turn on")])

    assert resp.stop_reason == "tool_use"
    assert len(resp.tool_uses) == 1
    use = resp.tool_uses[0]
    assert use.id == "call_1"
    assert use.name == "call_service"
    assert use.input == {"domain": "light", "service": "turn_on"}


async def test_complete_synthesises_id_when_ollama_omits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older Ollama versions don't always emit ``id``; we mint one."""

    provider = OllamaProvider(default_model="llama3.1")
    chat = AsyncMock(
        return_value=_ok_response(
            text="",
            tool_calls=[
                {
                    "function": {
                        "name": "thing",
                        "arguments": {"k": "v"},
                    },
                }
            ],
        )
    )
    monkeypatch.setattr(provider._client, "chat", chat)

    resp = await provider.complete([ChatMessage(role="user", content="?")])
    assert resp.tool_uses[0].id.startswith("ollama_tc_")


# --- tool-argument coercion (unit) ------------------------------------------


def test_coerce_tool_arguments_passes_dict_through() -> None:
    assert _coerce_tool_arguments({"a": 1}) == {"a": 1}


def test_coerce_tool_arguments_parses_json_string() -> None:
    assert _coerce_tool_arguments('{"a": 1}') == {"a": 1}


def test_coerce_tool_arguments_returns_raw_on_bad_json() -> None:
    assert _coerce_tool_arguments("not-json") == {"_raw": "not-json"}


def test_coerce_tool_arguments_returns_empty_on_unknown_type() -> None:
    assert _coerce_tool_arguments(42) == {}


# --- message translation (unit) ---------------------------------------------


def test_messages_to_ollama_assistant_with_tool_calls() -> None:
    msg = ChatMessage(
        role="assistant",
        content="picking a light",
        tool_uses=[ToolUse(id="call_1", name="t", input={"k": "v"})],
    )
    out = _messages_to_ollama([msg], system=None)
    assert out == [
        {
            "role": "assistant",
            "content": "picking a light",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "t", "arguments": {"k": "v"}},
                }
            ],
        }
    ]


def test_messages_to_ollama_tool_results_become_role_tool() -> None:
    msg = ChatMessage(
        role="user",
        tool_results=[ToolResult(tool_use_id="call_1", content="ok")],
    )
    assert _messages_to_ollama([msg], system=None) == [
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"}
    ]


def test_messages_to_ollama_inline_system_messages_are_hoisted() -> None:
    out = _messages_to_ollama(
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
    os.environ.get("CAESAR_LLM__OLLAMA__BASE_URL") is None,
    reason="Live Ollama integration test (set CAESAR_LLM__OLLAMA__BASE_URL to run).",
)
async def test_live_ollama_round_trip() -> None:  # pragma: no cover - live
    """Optional integration test; only runs when an Ollama base URL is in env."""

    provider = OllamaProvider(
        default_model=os.environ.get("CAESAR_LLM__OLLAMA__MODEL", "llama3.1:8b-instruct"),
        default_max_tokens=64,
        base_url=os.environ["CAESAR_LLM__OLLAMA__BASE_URL"],
    )
    resp = await provider.complete(
        [ChatMessage(role="user", content="Say 'pong' and nothing else.")]
    )
    assert "pong" in resp.content.lower()
