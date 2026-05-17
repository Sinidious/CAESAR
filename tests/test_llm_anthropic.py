from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from caesar.llm.anthropic import AnthropicProvider
from caesar.llm.gateway import ChatMessage


def _fake_anthropic_response(text: str = "hi", model: str = "claude-test"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        model=model,
        usage=SimpleNamespace(input_tokens=5, output_tokens=7),
    )


@pytest.fixture
def fake_messages_create():
    from anthropic.types import TextBlock

    async def _create(**kwargs: Any) -> Any:
        return SimpleNamespace(
            content=[TextBlock(type="text", text="hi", citations=None)],
            model="claude-test",
            usage=SimpleNamespace(input_tokens=5, output_tokens=7),
            stop_reason="end_turn",
        )

    return AsyncMock(side_effect=_create)


async def test_complete_passes_args_and_maps_response(
    monkeypatch: pytest.MonkeyPatch, fake_messages_create: AsyncMock
):
    provider = AnthropicProvider(
        api_key="sk-test",
        default_model="claude-default",
        default_max_tokens=512,
    )
    monkeypatch.setattr(provider._client.messages, "create", fake_messages_create)

    resp = await provider.complete(
        [ChatMessage(role="user", content="hello")],
        system="You are CAESAR.",
        model="claude-override",
        max_tokens=128,
    )

    assert resp.content == "hi"
    assert resp.model == "claude-test"
    assert resp.input_tokens == 5
    assert resp.output_tokens == 7

    assert fake_messages_create.await_args is not None
    kwargs = fake_messages_create.await_args.kwargs
    assert kwargs["model"] == "claude-override"
    assert kwargs["max_tokens"] == 128
    assert kwargs["system"] == "You are CAESAR."
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]


async def test_complete_uses_defaults(
    monkeypatch: pytest.MonkeyPatch, fake_messages_create: AsyncMock
):
    provider = AnthropicProvider(
        api_key="sk-test",
        default_model="claude-default",
        default_max_tokens=256,
    )
    monkeypatch.setattr(provider._client.messages, "create", fake_messages_create)

    await provider.complete([ChatMessage(role="user", content="hello")])

    assert fake_messages_create.await_args is not None
    kwargs = fake_messages_create.await_args.kwargs
    assert kwargs["model"] == "claude-default"
    assert kwargs["max_tokens"] == 256
    # No system supplied → NOT_GIVEN sentinel.
    assert kwargs["system"].__class__.__name__ == "NotGiven"


async def test_complete_concatenates_system_messages(
    monkeypatch: pytest.MonkeyPatch, fake_messages_create: AsyncMock
):
    provider = AnthropicProvider(api_key="sk-test", default_model="claude-default")
    monkeypatch.setattr(provider._client.messages, "create", fake_messages_create)

    await provider.complete(
        [
            ChatMessage(role="system", content="Stay concise."),
            ChatMessage(role="user", content="hi"),
        ],
        system="You are CAESAR.",
    )

    assert fake_messages_create.await_args is not None
    kwargs = fake_messages_create.await_args.kwargs
    assert kwargs["system"] == "You are CAESAR.\n\nStay concise."
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


async def test_complete_passes_tools_when_supplied(
    monkeypatch: pytest.MonkeyPatch, fake_messages_create: AsyncMock
):
    from caesar.llm.gateway import ToolDefinition

    provider = AnthropicProvider(api_key="sk-test", default_model="claude-default")
    monkeypatch.setattr(provider._client.messages, "create", fake_messages_create)

    await provider.complete(
        [ChatMessage(role="user", content="hi")],
        tools=[
            ToolDefinition(
                name="call_service",
                description="d",
                input_schema={"type": "object"},
            )
        ],
    )

    assert fake_messages_create.await_args is not None
    kwargs = fake_messages_create.await_args.kwargs
    assert kwargs["tools"] == [
        {
            "name": "call_service",
            "description": "d",
            "input_schema": {"type": "object"},
        }
    ]


async def test_complete_serializes_assistant_tool_use(
    monkeypatch: pytest.MonkeyPatch, fake_messages_create: AsyncMock
):
    from caesar.llm.gateway import ToolUse

    provider = AnthropicProvider(api_key="sk-test", default_model="claude-default")
    monkeypatch.setattr(provider._client.messages, "create", fake_messages_create)

    await provider.complete(
        [
            ChatMessage(role="user", content="turn it on"),
            ChatMessage(
                role="assistant",
                content="Doing that now.",
                tool_uses=[ToolUse(id="t1", name="call_service", input={"x": 1})],
            ),
        ]
    )

    assert fake_messages_create.await_args is not None
    sent_messages = fake_messages_create.await_args.kwargs["messages"]
    assistant_msg = sent_messages[1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"] == [
        {"type": "text", "text": "Doing that now."},
        {"type": "tool_use", "id": "t1", "name": "call_service", "input": {"x": 1}},
    ]


async def test_complete_serializes_assistant_tool_use_without_text(
    monkeypatch: pytest.MonkeyPatch, fake_messages_create: AsyncMock
):
    """Assistant tool_use turn with no narration content."""

    from caesar.llm.gateway import ToolUse

    provider = AnthropicProvider(api_key="sk-test", default_model="claude-default")
    monkeypatch.setattr(provider._client.messages, "create", fake_messages_create)

    await provider.complete(
        [
            ChatMessage(role="user", content="x"),
            ChatMessage(
                role="assistant",
                tool_uses=[ToolUse(id="t9", name="call_service", input={})],
            ),
        ]
    )

    assert fake_messages_create.await_args is not None
    assistant_msg = fake_messages_create.await_args.kwargs["messages"][1]
    # No leading text block when content is empty.
    assert assistant_msg["content"] == [
        {"type": "tool_use", "id": "t9", "name": "call_service", "input": {}},
    ]


async def test_complete_serializes_user_tool_results(
    monkeypatch: pytest.MonkeyPatch, fake_messages_create: AsyncMock
):
    from caesar.llm.gateway import ToolResult

    provider = AnthropicProvider(api_key="sk-test", default_model="claude-default")
    monkeypatch.setattr(provider._client.messages, "create", fake_messages_create)

    await provider.complete(
        [
            ChatMessage(
                role="user",
                content="here is the result",
                tool_results=[ToolResult(tool_use_id="t1", content="ok", is_error=False)],
            )
        ]
    )

    assert fake_messages_create.await_args is not None
    sent = fake_messages_create.await_args.kwargs["messages"][0]
    assert sent["role"] == "user"
    assert sent["content"] == [
        {"type": "text", "text": "here is the result"},
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False},
    ]


async def test_complete_serializes_user_tool_results_without_text(
    monkeypatch: pytest.MonkeyPatch, fake_messages_create: AsyncMock
):
    from caesar.llm.gateway import ToolResult

    provider = AnthropicProvider(api_key="sk-test", default_model="claude-default")
    monkeypatch.setattr(provider._client.messages, "create", fake_messages_create)

    await provider.complete(
        [
            ChatMessage(
                role="user",
                tool_results=[ToolResult(tool_use_id="t1", content="ok")],
            )
        ]
    )

    assert fake_messages_create.await_args is not None
    sent = fake_messages_create.await_args.kwargs["messages"][0]
    # No leading text block when ChatMessage has no content.
    assert sent["content"] == [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False},
    ]


async def test_complete_serializes_assistant_text_only(
    monkeypatch: pytest.MonkeyPatch, fake_messages_create: AsyncMock
):
    """Assistant message with no tool_uses keeps the plain string form."""

    provider = AnthropicProvider(api_key="sk-test", default_model="claude-default")
    monkeypatch.setattr(provider._client.messages, "create", fake_messages_create)

    await provider.complete(
        [
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hello"),
        ]
    )
    assert fake_messages_create.await_args is not None
    assistant_msg = fake_messages_create.await_args.kwargs["messages"][1]
    assert assistant_msg["content"] == "hello"


async def test_complete_parses_tool_use_block_in_response(
    monkeypatch: pytest.MonkeyPatch,
):
    """When Anthropic returns a ToolUseBlock, we expose it on ChatResponse."""

    from types import SimpleNamespace

    from anthropic.types import TextBlock, ToolUseBlock

    async def _tool_create(**_: object) -> object:
        return SimpleNamespace(
            content=[
                TextBlock(type="text", text="calling tool", citations=None),
                ToolUseBlock(
                    type="tool_use",
                    id="tu_99",
                    name="call_service",
                    input={"domain": "light", "service": "turn_on"},
                ),
            ],
            model="claude-test",
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
            stop_reason="tool_use",
        )

    provider = AnthropicProvider(api_key="sk-test", default_model="claude-default")
    monkeypatch.setattr(provider._client.messages, "create", AsyncMock(side_effect=_tool_create))

    resp = await provider.complete([ChatMessage(role="user", content="x")])
    assert resp.stop_reason == "tool_use"
    assert resp.tool_uses[0].id == "tu_99"
    assert resp.tool_uses[0].input == {"domain": "light", "service": "turn_on"}
    assert resp.content == "calling tool"


@pytest.mark.skipif(
    not os.getenv("CAESAR_LLM__API_KEY"),
    reason="Live Anthropic integration test (set CAESAR_LLM__API_KEY to run).",
)
async def test_live_anthropic_echo():  # pragma: no cover - opt-in
    from caesar.config import get_settings, reset_settings_cache

    reset_settings_cache()
    settings = get_settings()
    assert settings.llm.api_key is not None
    provider = AnthropicProvider(
        api_key=settings.llm.api_key.get_secret_value(),
        default_model=settings.llm.model,
        default_max_tokens=64,
    )
    resp = await provider.complete(
        [ChatMessage(role="user", content="reply with the single word: pong")]
    )
    assert "pong" in resp.content.lower()
