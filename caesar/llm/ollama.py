"""Ollama provider for the LLM Gateway (ADR-0026).

Fully-local operation: the model lives on the operator's box, no
third-party traffic. Uses the official ``ollama`` Python client's
async interface. Tool calling is supported on Ollama 0.4+ via the
same OpenAI-shaped ``tools=[{type:"function", function: {...}}]``
schema, so the translation logic is structurally similar to
:mod:`caesar.llm.openai`.

Token accounting normalises Ollama's ``prompt_eval_count`` /
``eval_count`` to our ``input_tokens`` / ``output_tokens`` naming
per ADR-0026. Ollama doesn't currently expose reasoning tokens
separately; the model's full visible output counts toward
``output_tokens``.

Models that don't advertise tool support will reject tools in the
request — Ollama returns an error which surfaces as the underlying
SDK exception. Operators pick a tools-capable model (e.g.
``llama3.1:8b-instruct``, ``qwen2.5``, ``mistral-nemo``).
"""

from __future__ import annotations

import json
from typing import Any, cast

from ollama import AsyncClient

from caesar.llm.gateway import (
    ChatMessage,
    ChatResponse,
    StopReason,
    ToolDefinition,
    ToolUse,
)


def _tool_definitions_to_ollama(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
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


def _messages_to_ollama(messages: list[ChatMessage], *, system: str | None) -> list[dict[str, Any]]:
    """Translate ChatMessage list into Ollama's chat wire shape.

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
            entry: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_uses:
                entry["tool_calls"] = [
                    {
                        "id": use.id,
                        "type": "function",
                        "function": {
                            "name": use.name,
                            "arguments": use.input,
                        },
                    }
                    for use in msg.tool_uses
                ]
            out.append(entry)
            continue

        # role == "user". Tool results re-enter as `role="tool"`.
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


def _coerce_tool_arguments(raw: Any) -> dict[str, Any]:
    """Ollama returns tool arguments as a dict in newer versions and as
    a JSON string in older ones; accept either."""

    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
            if isinstance(parsed, dict):
                return cast(dict[str, Any], parsed)
        except json.JSONDecodeError:
            return {"_raw": raw}
    return {}


class OllamaProvider:
    """Wraps :class:`ollama.AsyncClient` behind the gateway Protocol."""

    def __init__(
        self,
        *,
        default_model: str,
        default_max_tokens: int = 1024,
        base_url: str = "http://localhost:11434",
    ) -> None:
        self._client = AsyncClient(host=base_url)
        self._default_model = default_model
        self._default_max_tokens = default_max_tokens
        self._base_url = base_url

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
        task: str | None = None,
    ) -> ChatResponse:
        del task  # routing hint; only TaskRouter uses it
        used_model = model or self._default_model
        used_max_tokens = max_tokens or self._default_max_tokens

        chat_messages = _messages_to_ollama(messages, system=system)
        kwargs: dict[str, Any] = {
            "model": used_model,
            "messages": chat_messages,
            "options": {"num_predict": used_max_tokens},
        }
        if tools:
            kwargs["tools"] = _tool_definitions_to_ollama(tools)

        resp = await self._client.chat(**kwargs)

        # The SDK returns either an ollama.ChatResponse pydantic model
        # (newer versions) or a plain dict (older). Treat the dict
        # version as canonical and fall back to attribute access.
        if isinstance(resp, dict):
            message = resp.get("message", {})
            model_name = resp.get("model", used_model)
            prompt_eval = int(resp.get("prompt_eval_count", 0) or 0)
            eval_count = int(resp.get("eval_count", 0) or 0)
            done_reason = resp.get("done_reason", "stop")
        else:
            message = getattr(resp, "message", None) or {}
            if not isinstance(message, dict):
                message = {
                    "content": getattr(message, "content", "") or "",
                    "tool_calls": getattr(message, "tool_calls", None),
                }
            model_name = getattr(resp, "model", used_model)
            prompt_eval = int(getattr(resp, "prompt_eval_count", 0) or 0)
            eval_count = int(getattr(resp, "eval_count", 0) or 0)
            done_reason = getattr(resp, "done_reason", "stop")

        text = message.get("content") or ""
        raw_calls = message.get("tool_calls") or []

        tool_uses: list[ToolUse] = []
        for index, call in enumerate(raw_calls):
            func = call.get("function") if isinstance(call, dict) else getattr(call, "function", {})
            if not isinstance(func, dict):
                func = {
                    "name": getattr(func, "name", ""),
                    "arguments": getattr(func, "arguments", {}),
                }
            call_id = (
                call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
            ) or f"ollama_tc_{index}"
            tool_uses.append(
                ToolUse(
                    id=str(call_id),
                    name=func.get("name", ""),
                    input=_coerce_tool_arguments(func.get("arguments", {})),
                )
            )

        if tool_uses:
            stop_reason: StopReason = "tool_use"
        elif done_reason == "length":
            stop_reason = "max_tokens"
        else:
            stop_reason = "end_turn"

        return ChatResponse(
            content=text,
            model=str(model_name),
            input_tokens=prompt_eval,
            output_tokens=eval_count,
            stop_reason=stop_reason,
            tool_uses=tool_uses,
        )
