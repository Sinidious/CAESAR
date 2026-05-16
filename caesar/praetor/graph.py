"""LangGraph "echo" state machine (ADR-0006).

One node, one edge to END. The point of using LangGraph here at v0.1
is to set the pattern: every brain decision flows through a compiled
graph, every node binds the surrounding decision id so its logs are
correlatable (ADR-0018), and adding intent/routing/policy nodes later
is a matter of inserting them on the path.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from caesar.llm.gateway import ChatMessage, ChatResponse, LLMGateway
from caesar.log import bind_decision, get_logger


class EchoState(TypedDict, total=False):
    """State carried between graph nodes for a single decision."""

    messages: list[ChatMessage]
    system: str | None
    model: str | None
    decision_id: str
    response: ChatResponse


def build_echo_graph(gateway: LLMGateway) -> Any:
    """Compile the echo graph against the supplied gateway."""

    logger = get_logger("caesar.praetor.graph")

    async def call_llm(state: EchoState) -> EchoState:
        decision_id = state["decision_id"]
        with bind_decision(decision_id):
            logger.info(
                "graph.node.start",
                node="call_llm",
                model=state.get("model"),
                turns=len(state["messages"]),
            )
            response = await gateway.complete(
                state["messages"],
                system=state.get("system"),
                model=state.get("model"),
            )
            logger.info(
                "graph.node.end",
                node="call_llm",
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
            return {"response": response}

    graph: StateGraph[EchoState] = StateGraph(EchoState)
    graph.add_node("call_llm", call_llm)
    graph.set_entry_point("call_llm")
    graph.add_edge("call_llm", END)
    return graph.compile()
