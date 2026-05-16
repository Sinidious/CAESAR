from __future__ import annotations

from caesar.llm.gateway import ChatMessage, ChatResponse


def test_chat_message_defaults_to_empty_text() -> None:
    """ChatMessage may carry only tool_uses/tool_results; empty content is OK."""

    m = ChatMessage(role="user")
    assert m.content == ""
    assert m.tool_uses == []
    assert m.tool_results == []


def test_chat_message_roundtrips() -> None:
    m = ChatMessage(role="user", content="hello")
    dumped = m.model_dump()
    assert dumped["role"] == "user"
    assert dumped["content"] == "hello"


def test_chat_response_holds_usage() -> None:
    r = ChatResponse(content="hi", model="x", input_tokens=3, output_tokens=4)
    assert r.input_tokens == 3
    assert r.output_tokens == 4
