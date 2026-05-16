from __future__ import annotations

from caesar.llm.gateway import (
    ChatMessage,
    ChatResponse,
    ToolDefinition,
    ToolResult,
    ToolUse,
)


def test_tool_definition_carries_schema() -> None:
    t = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
    assert t.input_schema == {"type": "object"}


def test_chat_message_can_carry_tool_uses() -> None:
    m = ChatMessage(
        role="assistant",
        content="thinking...",
        tool_uses=[ToolUse(id="t1", name="call_service", input={"d": 1})],
    )
    assert m.tool_uses[0].id == "t1"
    assert m.tool_uses[0].input == {"d": 1}


def test_chat_message_can_carry_tool_results() -> None:
    m = ChatMessage(
        role="user",
        tool_results=[ToolResult(tool_use_id="t1", content="ok")],
    )
    assert m.tool_results[0].is_error is False


def test_chat_response_defaults_to_end_turn() -> None:
    r = ChatResponse(content="hi", model="x", input_tokens=1, output_tokens=2)
    assert r.stop_reason == "end_turn"
    assert r.tool_uses == []
