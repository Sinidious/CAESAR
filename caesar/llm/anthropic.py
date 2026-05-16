"""Anthropic provider for the LLM Gateway (ADR-0011).

Uses the official ``anthropic`` SDK's async client. Translates our
gateway types to and from the SDK's message and tool blocks.

System messages are passed out-of-band via the ``system`` argument
per Anthropic's API; any ``role="system"`` messages in the input list
are concatenated into the effective system prompt.
"""

from __future__ import annotations

from typing import Any, cast

from anthropic import NOT_GIVEN, AsyncAnthropic
from anthropic.types import TextBlock, ToolUseBlock

from caesar.llm.gateway import (
    ChatMessage,
    ChatResponse,
    StopReason,
    ToolDefinition,
    ToolUse,
)


def _message_to_anthropic(msg: ChatMessage) -> dict[str, Any]:
    """Convert one ChatMessage to Anthropic's wire shape."""

    if msg.role == "assistant":
        if not msg.tool_uses:
            return {"role": "assistant", "content": msg.content}
        blocks: list[dict[str, Any]] = []
        if msg.content:
            blocks.append({"type": "text", "text": msg.content})
        for use in msg.tool_uses:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": use.id,
                    "name": use.name,
                    "input": use.input,
                }
            )
        return {"role": "assistant", "content": blocks}

    # User-side messages may carry tool results.
    if msg.tool_results:
        blocks = [
            {
                "type": "tool_result",
                "tool_use_id": r.tool_use_id,
                "content": r.content,
                "is_error": r.is_error,
            }
            for r in msg.tool_results
        ]
        if msg.content:
            blocks.insert(0, {"type": "text", "text": msg.content})
        return {"role": "user", "content": blocks}

    return {"role": "user", "content": msg.content}


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
        tools: list[ToolDefinition] | None = None,
    ) -> ChatResponse:
        system_parts: list[str] = [system] if system else []
        chat_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
                continue
            chat_messages.append(_message_to_anthropic(m))

        used_model = model or self._default_model
        used_max_tokens = max_tokens or self._default_max_tokens
        system_arg = "\n\n".join(system_parts) if system_parts else NOT_GIVEN

        tools_arg: Any = NOT_GIVEN
        if tools:
            tools_arg = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in tools
            ]

        resp = await self._client.messages.create(
            model=used_model,
            max_tokens=used_max_tokens,
            system=system_arg,  # type: ignore[arg-type]
            messages=chat_messages,  # type: ignore[arg-type]
            tools=tools_arg,
        )

        text_parts: list[str] = []
        tool_uses: list[ToolUse] = []
        for block in resp.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):  # pragma: no branch
                tool_uses.append(
                    ToolUse(
                        id=block.id,
                        name=block.name,
                        input=cast(dict[str, Any], block.input),
                    )
                )

        return ChatResponse(
            content="".join(text_parts),
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            stop_reason=cast(StopReason, resp.stop_reason or "end_turn"),
            tool_uses=tool_uses,
        )
