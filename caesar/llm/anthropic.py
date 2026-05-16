"""Anthropic provider for the LLM Gateway (ADR-0011).

Uses the official ``anthropic`` SDK's async client. ``system`` is
passed out-of-band per Anthropic's API; any ``role="system"`` messages
in the input list are concatenated into the effective system prompt.
"""

from __future__ import annotations

from anthropic import NOT_GIVEN, AsyncAnthropic
from anthropic.types import TextBlock

from caesar.llm.gateway import ChatMessage, ChatResponse


class AnthropicProvider:
    """Wraps :class:`anthropic.AsyncAnthropic` behind the gateway."""

    def __init__(
        self,
        api_key: str,
        *,
        default_model: str,
        default_max_tokens: int = 1024,
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._default_model = default_model
        self._default_max_tokens = default_max_tokens

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        system_parts: list[str] = [system] if system else []
        chat_messages: list[dict[str, str]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                chat_messages.append({"role": m.role, "content": m.content})

        used_model = model or self._default_model
        used_max_tokens = max_tokens or self._default_max_tokens
        system_arg = "\n\n".join(system_parts) if system_parts else NOT_GIVEN

        resp = await self._client.messages.create(
            model=used_model,
            max_tokens=used_max_tokens,
            system=system_arg,  # type: ignore[arg-type]
            messages=chat_messages,  # type: ignore[arg-type]
        )

        text = "".join(block.text for block in resp.content if isinstance(block, TextBlock))
        return ChatResponse(
            content=text,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
