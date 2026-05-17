"""OpenAI provider for the LLM Gateway (ADR-0026).

Uses the official ``openai`` SDK's async client. Translates our
gateway types to and from OpenAI's chat-completions wire shape:

- :class:`ChatMessage` with ``role="user|assistant|system"`` maps
  directly. Assistant tool calls go in ``message.tool_calls`` (a
  list of ``{id, type, function: {name, arguments}}`` objects).
  User-side tool results become extra messages with ``role="tool"``
  and ``tool_call_id`` matching the assistant's emission.
- :class:`ToolDefinition` maps to ``tools=[{type:"function", function:
  {name, description, parameters}}]``.
- The response is normalised back to :class:`ChatResponse`, including
  any ``ToolUse`` calls the model emitted.

Token accounting normalises OpenAI's ``prompt_tokens`` /
``completion_tokens`` to our ``input_tokens`` / ``output_tokens``
naming. Reasoning tokens (when the model emits them) are bucketed
into ``output_tokens`` per ADR-0026.
"""

from __future__ import annotations

import json
from typing import Any, cast

from openai import AsyncOpenAI

from caesar.llm.gateway import (
    ChatMessage,
    ChatResponse,
    StopReason,
    ToolDefinition,
    ToolUse,
)

_STOP_REASON_MAP: dict[str, StopReason] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",  # legacy
    "content_filter": "stop_sequence",
}


def _tool_definitions_to_openai(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def _messages_to_openai(messages: list[ChatMessage], *, system: str | None) -> list[dict[str, Any]]:
    """Translate ChatMessage list into OpenAI's chat-completions shape.

    System content is hoisted to a leading ``role="system"`` message.
    Tool results are emitted as ``role="tool"`` messages keyed by the
    assistant's ``tool_call_id``.
    """

    out: list[dict[str, Any]] = []
    system_parts: list[str] = [system] if system else []

    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.content)
            continue

        if msg.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant"}
            if msg.content:
                entry["content"] = msg.content
            else:
                # OpenAI requires `content` to be present (can be null).
                entry["content"] = None
            if msg.tool_uses:
                entry["tool_calls"] = [
                    {
                        "id": use.id,
                        "type": "function",
                        "function": {
                            "name": use.name,
                            "arguments": json.dumps(use.input),
                        },
                    }
                    for use in msg.tool_uses
                ]
            out.append(entry)
            continue

        # role == "user". Tool results re-enter as `role="tool"`
        # messages; the user's free-text portion (if any) becomes a
        # separate `role="user"` message that follows.
        if msg.tool_results:
            for r in msg.tool_results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": r.tool_use_id,
                        "content": r.content,
                    }
                )
            if msg.content:
                out.append({"role": "user", "content": msg.content})
            continue

        out.append({"role": "user", "content": msg.content})

    if system_parts:
        out.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
    return out


class OpenAIProvider:
    """Wraps :class:`openai.AsyncOpenAI` behind the gateway Protocol."""

    def __init__(
        self,
        api_key: str,
        *,
        default_model: str,
        default_max_tokens: int = 1024,
        base_url: str | None = None,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
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
        used_model = model or self._default_model
        used_max_tokens = max_tokens or self._default_max_tokens

        chat_messages = _messages_to_openai(messages, system=system)
        tools_arg = _tool_definitions_to_openai(tools) if tools else None

        kwargs: dict[str, Any] = {
            "model": used_model,
            "max_completion_tokens": used_max_tokens,
            "messages": chat_messages,
        }
        if tools_arg is not None:
            kwargs["tools"] = tools_arg

        resp = await self._client.chat.completions.create(**kwargs)

        choice = resp.choices[0]
        message = choice.message
        text = message.content or ""

        tool_uses: list[ToolUse] = []
        for call in message.tool_calls or []:
            try:
                parsed = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                parsed = {"_raw": call.function.arguments}
            tool_uses.append(
                ToolUse(
                    id=call.id,
                    name=call.function.name,
                    input=cast(dict[str, Any], parsed),
                )
            )

        usage = resp.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        # Reasoning models expose `completion_tokens_details.reasoning_tokens`.
        # Roll into output_tokens per ADR-0026 so the existing
        # Prometheus histogram stays comparable.
        details = getattr(usage, "completion_tokens_details", None) if usage else None
        reasoning = getattr(details, "reasoning_tokens", None) if details else None
        if reasoning:
            output_tokens += reasoning

        stop_reason: StopReason = _STOP_REASON_MAP.get(choice.finish_reason or "stop", "end_turn")

        return ChatResponse(
            content=text,
            model=resp.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
            tool_uses=tool_uses,
        )
