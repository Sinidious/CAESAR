"""Gateway types and the provider Protocol (ADR-0011).

Tests use a fake gateway that satisfies the Protocol; production wires
in :class:`caesar.llm.anthropic.AnthropicProvider`.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    """One turn in a conversation."""

    role: Role
    content: str = Field(min_length=1)


class ChatResponse(BaseModel):
    """The provider's reply plus accounting metadata."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int


class LLMGateway(Protocol):
    """Async chat-completion contract every provider must satisfy."""

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        """Return the assistant's reply for the given conversation."""
        ...
