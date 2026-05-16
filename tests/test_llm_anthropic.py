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
