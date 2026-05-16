"""Gateway types and the provider Protocol (ADR-0011).

Tests use a fake gateway that satisfies the Protocol; production wires
in :class:`caesar.llm.anthropic.AnthropicProvider`.

Tool-use types are present so the brain graph (ADR-0006) can let the
model invoke ``call_service`` and receive results in the same
conversation. Providers translate these to/from their native shapes.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]

StopReason = Literal["end_turn", "tool_use", "max_tokens", "stop_sequence"]


class ToolDefinition(BaseModel):
    """Tool the model may invoke during a completion."""

    name: str = Field(min_length=1)
    description: str
    input_schema: dict[str, Any]


class ToolUse(BaseModel):
    """One tool invocation emitted by the assistant."""

    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Outcome of executing a previous :class:`ToolUse`."""

    tool_use_id: str
    content: str
    is_error: bool = False


class ChatMessage(BaseModel):
    """One turn in a conversation.

    ``content`` carries free text (default). ``tool_uses`` is populated
    on assistant turns where the model invoked tools; ``tool_results``
    is populated on user turns that report tool outcomes back to the
    model. The three fields can coexist (e.g. an assistant message with
    a short narration plus a tool call).
    """

    role: Role
    content: str = ""
    tool_uses: list[ToolUse] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """The provider's reply plus accounting metadata."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: StopReason = "end_turn"
    tool_uses: list[ToolUse] = Field(default_factory=list)


class LLMGateway(Protocol):
    """Async chat-completion contract every provider must satisfy."""

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> ChatResponse:
        """Return the assistant's reply for the given conversation."""
        ...
