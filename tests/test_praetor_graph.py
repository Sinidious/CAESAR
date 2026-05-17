from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.ha.client import HAClient
from caesar.llm.gateway import ChatMessage, ChatResponse, ToolUse
from caesar.policy.allowlist import AllowlistPolicy
from caesar.policy.engine import DenyAllPolicy, Policy
from caesar.policy.yaml_loader import RulesConfig
from caesar.praetor.graph import build_brain_graph
from tests.conftest import FakeGateway


def _allow_light_policy() -> Policy:
    return AllowlistPolicy(
        RulesConfig(version=1, allowed_services=["light.turn_on", "light.turn_off"])
    )


async def test_no_tool_use_returns_final_text(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    audit = AuditLogger(engine)
    graph = build_brain_graph(gateway=fake_gateway, ha=None, policy=DenyAllPolicy(), audit=audit)
    state = await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="hello")],
            "system": "test",
            "model": "fake-model",
            "decision_id": "d-1",
            "iteration": 0,
        }
    )
    assert state["response"].content == "hello back"
    # When HA is None, the gateway is called with tools=None.
    assert fake_gateway.calls[0]["tools"] is None


async def test_single_tool_use_dispatches_and_loops(
    fake_gateway: FakeGateway,
    engine: AsyncEngine,
    mock_ha: HAClient,
    ha_service_calls: list[dict[str, Any]],
) -> None:
    audit = AuditLogger(engine)

    # First call: model emits a tool_use. Second call: model finishes.
    fake_gateway.queue(
        ChatResponse(
            content="",
            model="fake-model",
            input_tokens=1,
            output_tokens=2,
            stop_reason="tool_use",
            tool_uses=[
                ToolUse(
                    id="tu_1",
                    name="call_service",
                    input={"domain": "light", "service": "turn_on"},
                )
            ],
        )
    )
    fake_gateway.queue(
        ChatResponse(
            content="Kitchen light is on.",
            model="fake-model",
            input_tokens=3,
            output_tokens=4,
        )
    )

    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=mock_ha,
        policy=_allow_light_policy(),
        audit=audit,
    )
    state = await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="turn on the light")],
            "system": "test",
            "model": "fake-model",
            "decision_id": "d-tool",
            "iteration": 0,
        }
    )
    assert state["response"].content == "Kitchen light is on."
    assert ha_service_calls[0]["domain"] == "light"
    # The gateway saw tools on each invocation (HA is configured).
    assert fake_gateway.calls[0]["tools"] is not None
    # Second call's last message should be a tool_result.
    second_messages = fake_gateway.calls[1]["messages"]
    assert isinstance(second_messages, list)
    assert second_messages[-1].tool_results[0].tool_use_id == "tu_1"


async def test_denied_tool_yields_error_result(
    fake_gateway: FakeGateway, engine: AsyncEngine, mock_ha: HAClient
) -> None:
    audit = AuditLogger(engine)
    fake_gateway.queue(
        ChatResponse(
            content="",
            model="fake-model",
            input_tokens=1,
            output_tokens=2,
            stop_reason="tool_use",
            tool_uses=[
                ToolUse(
                    id="tu_2",
                    name="call_service",
                    input={"domain": "lock", "service": "unlock"},
                )
            ],
        )
    )
    fake_gateway.queue(
        ChatResponse(
            content="Sorry, I can't unlock that.",
            model="fake-model",
            input_tokens=3,
            output_tokens=4,
        )
    )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=mock_ha,
        policy=_allow_light_policy(),  # locks are NOT allowed
        audit=audit,
    )
    state = await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="unlock the door")],
            "decision_id": "d-deny",
            "iteration": 0,
        }
    )
    assert state["response"].content == "Sorry, I can't unlock that."
    # The tool_result the model saw must be flagged as error.
    user_msg_with_result = fake_gateway.calls[1]["messages"][-1]
    assert user_msg_with_result.tool_results[0].is_error is True
    assert "Denied" in user_msg_with_result.tool_results[0].content


async def test_unknown_tool_yields_error_result(
    fake_gateway: FakeGateway, engine: AsyncEngine, mock_ha: HAClient
) -> None:
    audit = AuditLogger(engine)
    fake_gateway.queue(
        ChatResponse(
            content="",
            model="fake-model",
            input_tokens=1,
            output_tokens=2,
            stop_reason="tool_use",
            tool_uses=[ToolUse(id="tu_x", name="unknown_tool", input={})],
        )
    )
    fake_gateway.queue(
        ChatResponse(content="ok", model="fake-model", input_tokens=2, output_tokens=2)
    )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=mock_ha,
        policy=_allow_light_policy(),
        audit=audit,
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="do something weird")],
            "decision_id": "d-unknown",
            "iteration": 0,
        }
    )
    result = fake_gateway.calls[1]["messages"][-1].tool_results[0]
    assert result.is_error is True
    assert "Unknown tool" in result.content


async def test_invalid_tool_input_yields_error_result(
    fake_gateway: FakeGateway, engine: AsyncEngine, mock_ha: HAClient
) -> None:
    audit = AuditLogger(engine)
    fake_gateway.queue(
        ChatResponse(
            content="",
            model="fake-model",
            input_tokens=1,
            output_tokens=2,
            stop_reason="tool_use",
            tool_uses=[ToolUse(id="tu_bad", name="call_service", input={"domain": ""})],
        )
    )
    fake_gateway.queue(
        ChatResponse(content="ok", model="fake-model", input_tokens=2, output_tokens=2)
    )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=mock_ha,
        policy=_allow_light_policy(),
        audit=audit,
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="bad call")],
            "decision_id": "d-bad",
            "iteration": 0,
        }
    )
    result = fake_gateway.calls[1]["messages"][-1].tool_results[0]
    assert result.is_error is True
    assert "Invalid call_service input" in result.content


async def test_recall_memory_tool_dispatches_via_registry(
    fake_gateway: FakeGateway, engine: AsyncEngine, bus, registry
) -> None:
    """When a memory.recall worker is registered, recall_memory is offered
    and the graph dispatches it through the registry."""

    import asyncio

    from caesar.legion.memory_recall import CAPABILITY, MemoryRecallWorker

    audit = AuditLogger(engine)
    worker = MemoryRecallWorker(bus, engine)
    await worker.start()
    try:
        for _ in range(50):
            if CAPABILITY in set(registry.capabilities()):
                break
            await asyncio.sleep(0.02)

        fake_gateway.queue(
            ChatResponse(
                content="",
                model="fake-model",
                input_tokens=1,
                output_tokens=2,
                stop_reason="tool_use",
                tool_uses=[ToolUse(id="r1", name="recall_memory", input={"limit": 5})],
            )
        )
        fake_gateway.queue(
            ChatResponse(
                content="No prior conversations yet.",
                model="fake-model",
                input_tokens=3,
                output_tokens=4,
            )
        )

        graph = build_brain_graph(
            gateway=fake_gateway,
            ha=None,
            policy=DenyAllPolicy(),
            audit=audit,
            registry=registry,
        )
        state = await graph.ainvoke(
            {
                "messages": [ChatMessage(role="user", content="what did we discuss?")],
                "decision_id": "d-recall",
                "iteration": 0,
            }
        )
        assert state["response"].content == "No prior conversations yet."

        first_tools = fake_gateway.calls[0]["tools"]
        assert first_tools is not None
        tool_names = [t.name for t in first_tools]
        assert "recall_memory" in tool_names
        assert "call_service" not in tool_names
    finally:
        await worker.stop()


async def test_recall_memory_failure_propagates_as_error_result(
    fake_gateway: FakeGateway, engine: AsyncEngine, bus, registry
) -> None:
    """When the worker errors, the brain sees an error tool_result."""

    import asyncio

    from caesar.legion.memory_recall import CAPABILITY, MemoryRecallWorker

    audit = AuditLogger(engine)
    worker = MemoryRecallWorker(bus, engine)
    await worker.start()
    try:
        for _ in range(50):
            if CAPABILITY in set(registry.capabilities()):
                break
            await asyncio.sleep(0.02)

        fake_gateway.queue(
            ChatResponse(
                content="",
                model="fake-model",
                input_tokens=1,
                output_tokens=2,
                stop_reason="tool_use",
                tool_uses=[ToolUse(id="r-bad", name="recall_memory", input={"limit": "lots"})],
            )
        )
        fake_gateway.queue(
            ChatResponse(
                content="Sorry, that didn't work.",
                model="fake-model",
                input_tokens=3,
                output_tokens=4,
            )
        )

        graph = build_brain_graph(
            gateway=fake_gateway,
            ha=None,
            policy=DenyAllPolicy(),
            audit=audit,
            registry=registry,
        )
        await graph.ainvoke(
            {
                "messages": [ChatMessage(role="user", content="recall")],
                "decision_id": "d-recall-bad",
                "iteration": 0,
            }
        )
        result = fake_gateway.calls[1]["messages"][-1].tool_results[0]
        assert result.is_error is True
        assert "recall_memory failed" in result.content
    finally:
        await worker.stop()


async def test_iteration_cap_bails_out(
    fake_gateway: FakeGateway, engine: AsyncEngine, mock_ha: HAClient
) -> None:
    """A model that keeps emitting tool_use must not run forever."""

    audit = AuditLogger(engine)
    # Queue 10 tool_use responses; max_iterations=3 should stop early.
    for i in range(10):
        fake_gateway.queue(
            ChatResponse(
                content="",
                model="fake-model",
                input_tokens=1,
                output_tokens=2,
                stop_reason="tool_use",
                tool_uses=[
                    ToolUse(
                        id=f"tu_{i}",
                        name="call_service",
                        input={"domain": "light", "service": "turn_on"},
                    )
                ],
            )
        )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=mock_ha,
        policy=_allow_light_policy(),
        audit=audit,
        max_iterations=3,
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="loop forever")],
            "decision_id": "d-cap",
            "iteration": 0,
        }
    )
    # 3 LLM calls, then bail.
    assert len(fake_gateway.calls) == 3
