"""Praetor's brain graph (ADR-0006).

A small LangGraph state machine with two nodes:

1. ``call_llm`` — hand the conversation to the LLM Gateway, with
   whatever tools are currently available.
2. ``dispatch_tools`` — execute any tool invocations the model emitted
   and append the results to the conversation so the model can react.

The graph loops between the two until the model returns a plain
``end_turn`` response or the iteration cap is hit. The cap exists so
a misbehaving model can't tie up Praetor forever.

Available tools depend on configuration:

- ``call_service`` is registered when the HA Bridge is configured;
  dispatches go through :func:`dispatch_service_call`
  (Policy → HA → Audit).
- ``recall_memory`` is registered when the registry has a
  ``memory.recall`` worker; dispatches via the registry over NATS.
"""

from __future__ import annotations

import json
from operator import add
from typing import Annotated, Any

from langgraph.graph import END, StateGraph
from pydantic import ValidationError
from typing_extensions import TypedDict

from caesar.db.audit import AuditLogger
from caesar.ha.client import HAClient
from caesar.ha.models import ServiceCall
from caesar.legion.registry import WorkerRegistry
from caesar.llm.gateway import (
    ChatMessage,
    ChatResponse,
    LLMGateway,
    ToolDefinition,
    ToolResult,
    ToolUse,
)
from caesar.log import bind_decision, get_logger
from caesar.policy.engine import Policy
from caesar.praetor.dispatch import dispatch_service_call

MAX_ITERATIONS_DEFAULT = 5

MEMORY_RECALL_CAPABILITY = "memory.recall"


class BrainState(TypedDict, total=False):
    """State carried between graph nodes for a single decision."""

    messages: Annotated[list[ChatMessage], add]
    system: str | None
    model: str | None
    decision_id: str
    response: ChatResponse
    iteration: int


CALL_SERVICE_TOOL = ToolDefinition(
    name="call_service",
    description=(
        "Invoke a Home Assistant service to control a device. Always "
        "specify both 'domain' (e.g. 'light') and 'service' (e.g. "
        "'turn_on'). Use 'target' to scope to specific entities, e.g. "
        '{"entity_id": "light.kitchen"}. The Policy Engine may deny '
        "the call; treat that as feedback to the user, not an error."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {"type": "string"},
            "service": {"type": "string"},
            "target": {"type": "object"},
            "data": {"type": "object"},
        },
        "required": ["domain", "service"],
    },
)


RECALL_MEMORY_TOOL = ToolDefinition(
    name="recall_memory",
    description=(
        "Look up recent CAESAR events from the audit log to recover "
        "context about prior conversations and decisions. Returns the "
        "newest events first. Use 'event_type' to filter, e.g. "
        '"chat.completed" for past chat replies or "service.called" '
        "for past device actions."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1},
            "event_type": {"type": "string"},
        },
    },
)


def build_brain_graph(
    *,
    gateway: LLMGateway,
    ha: HAClient | None,
    policy: Policy,
    audit: AuditLogger,
    registry: WorkerRegistry | None = None,
    max_iterations: int = MAX_ITERATIONS_DEFAULT,
) -> Any:
    """Compile the brain graph against the supplied collaborators."""

    logger = get_logger("caesar.praetor.brain")

    tools: list[ToolDefinition] = []
    if ha is not None:
        tools.append(CALL_SERVICE_TOOL)
    if registry is not None and registry.find(MEMORY_RECALL_CAPABILITY):
        tools.append(RECALL_MEMORY_TOOL)

    async def _handle_call_service(use: ToolUse, decision_id: str) -> ToolResult:
        try:
            call = ServiceCall.model_validate(use.input)
        except ValidationError as exc:
            return ToolResult(
                tool_use_id=use.id,
                content=f"Invalid call_service input: {exc}",
                is_error=True,
            )
        assert ha is not None  # invariant: tool only registered when ha set
        outcome = await dispatch_service_call(
            call, ha=ha, policy=policy, audit=audit, decision_id=decision_id
        )
        if outcome.decision.allowed:
            content = (
                f"OK: {call.domain}.{call.service} dispatched"
                f" (audit_log_id={outcome.audit_log_id})."
            )
            return ToolResult(tool_use_id=use.id, content=content, is_error=False)
        return ToolResult(
            tool_use_id=use.id,
            content=f"Denied: {outcome.decision.reason}",
            is_error=True,
        )

    async def _handle_recall_memory(use: ToolUse) -> ToolResult:
        assert registry is not None  # invariant: tool only registered when registry set
        result = await registry.dispatch(MEMORY_RECALL_CAPABILITY, use.input)
        if not result.success:
            return ToolResult(
                tool_use_id=use.id,
                content=f"recall_memory failed: {result.error}",
                is_error=True,
            )
        return ToolResult(
            tool_use_id=use.id,
            content=json.dumps(result.result or {}, default=str),
            is_error=False,
        )

    async def call_llm(state: BrainState) -> BrainState:
        with bind_decision(state["decision_id"]):
            iteration = state.get("iteration", 0)
            logger.info(
                "brain.node.call_llm",
                iteration=iteration,
                turns=len(state.get("messages", [])),
                tools=[t.name for t in tools],
            )
            response = await gateway.complete(
                state.get("messages", []),
                system=state.get("system"),
                model=state.get("model"),
                tools=tools or None,
            )
            assistant_msg = ChatMessage(
                role="assistant",
                content=response.content,
                tool_uses=response.tool_uses,
            )
            return {
                "messages": [assistant_msg],
                "response": response,
                "iteration": iteration + 1,
            }

    async def dispatch_tools(state: BrainState) -> BrainState:
        response = state["response"]
        decision_id = state["decision_id"]
        with bind_decision(decision_id):
            results: list[ToolResult] = []
            for use in response.tool_uses:
                if use.name == "call_service":
                    results.append(await _handle_call_service(use, decision_id))
                elif use.name == "recall_memory":
                    results.append(await _handle_recall_memory(use))
                else:
                    results.append(
                        ToolResult(
                            tool_use_id=use.id,
                            content=f"Unknown tool: {use.name}",
                            is_error=True,
                        )
                    )
            user_msg = ChatMessage(role="user", tool_results=results)
            logger.info(
                "brain.node.dispatch_tools",
                tools=[u.name for u in response.tool_uses],
                results=len(results),
            )
            return {"messages": [user_msg]}

    def route_after_llm(state: BrainState) -> str:
        iteration = state.get("iteration", 0)
        response = state["response"]
        if response.tool_uses and iteration < max_iterations:
            return "tools"
        return "end"

    graph: StateGraph[BrainState] = StateGraph(BrainState)
    graph.add_node("call_llm", call_llm)
    graph.add_node("dispatch_tools", dispatch_tools)
    graph.set_entry_point("call_llm")
    graph.add_conditional_edges(
        "call_llm",
        route_after_llm,
        {"tools": "dispatch_tools", "end": END},
    )
    graph.add_edge("dispatch_tools", "call_llm")
    return graph.compile()
