"""``/v1/chat`` — the v0.1 gate endpoint.

Accepts a list of messages, runs them through the echo graph, writes
an audit row, returns the assistant's reply plus the decision and
audit ids so callers can correlate.
"""

from __future__ import annotations

import uuid
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from caesar.config import CaesarSettings
from caesar.db.audit import AuditLogger
from caesar.llm.gateway import ChatMessage, ChatResponse, LLMGateway
from caesar.praetor.graph import build_echo_graph

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None


class ChatResponseBody(BaseModel):
    message: ChatMessage
    model: str
    decision_id: str
    audit_log_id: int
    usage: dict[str, int]


def _get_settings(request: Request) -> CaesarSettings:
    return cast(CaesarSettings, request.app.state.settings)


def _get_gateway(request: Request) -> LLMGateway:
    return cast(LLMGateway, request.app.state.gateway)


def _get_audit(request: Request) -> AuditLogger:
    return cast(AuditLogger, request.app.state.audit)


@router.post("/v1/chat", response_model=ChatResponseBody)
async def chat(
    body: ChatRequest,
    settings: Annotated[CaesarSettings, Depends(_get_settings)],
    gateway: Annotated[LLMGateway, Depends(_get_gateway)],
    audit: Annotated[AuditLogger, Depends(_get_audit)],
) -> ChatResponseBody:
    decision_id = uuid.uuid4().hex
    graph = build_echo_graph(gateway)
    state = await graph.ainvoke(
        {
            "messages": body.messages,
            "system": settings.llm.system_prompt,
            "model": body.model or settings.llm.model,
            "decision_id": decision_id,
        }
    )
    response: ChatResponse = state["response"]

    audit_id = await audit.record(
        "chat.completed",
        {
            "decision_id": decision_id,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "messages": [m.model_dump() for m in body.messages],
            "reply": response.content,
        },
    )

    return ChatResponseBody(
        message=ChatMessage(role="assistant", content=response.content),
        model=response.model,
        decision_id=decision_id,
        audit_log_id=audit_id,
        usage={
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        },
    )
