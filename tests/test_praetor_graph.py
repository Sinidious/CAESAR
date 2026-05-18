from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.ha.client import HAClient
from caesar.llm.gateway import ChatMessage, ChatResponse, ToolUse
from caesar.policy.allowlist import AllowlistPolicy
from caesar.policy.engine import DenyAllPolicy, Policy
from caesar.policy.yaml_loader import AllowedServiceRule, RulesConfig
from caesar.praetor.graph import build_brain_graph
from tests.conftest import FakeGateway


def _allow_light_policy() -> Policy:
    return AllowlistPolicy(
        RulesConfig(
            version=1,
            allowed_services=[
                AllowedServiceRule(service="light.turn_on"),
                AllowedServiceRule(service="light.turn_off"),
            ],
        )
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


async def test_semantic_recall_failure_propagates_as_error_result(
    fake_gateway: FakeGateway, engine: AsyncEngine, bus, registry
) -> None:
    """Worker errors flow back to the brain as is_error tool_results."""

    import asyncio

    from caesar.legion.semantic_recall import CAPABILITY, SemanticRecallWorker
    from caesar.llm.embeddings import StubEmbedder

    audit = AuditLogger(engine)
    worker = SemanticRecallWorker(bus, engine, StubEmbedder(dimension=32))
    await worker.start()
    try:
        for _ in range(50):
            if CAPABILITY in set(registry.capabilities()):
                break
            await asyncio.sleep(0.02)

        # Empty query → worker raises ValueError → TaskResult.success=False
        fake_gateway.queue(
            ChatResponse(
                content="",
                model="fake-model",
                input_tokens=1,
                output_tokens=2,
                stop_reason="tool_use",
                tool_uses=[ToolUse(id="s-bad", name="semantic_recall", input={"query": ""})],
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
                "decision_id": "d-sem-bad",
                "iteration": 0,
            }
        )
        result = fake_gateway.calls[1]["messages"][-1].tool_results[0]
        assert result.is_error is True
        assert "semantic_recall failed" in result.content
    finally:
        await worker.stop()


async def test_semantic_recall_tool_dispatches_via_registry(
    fake_gateway: FakeGateway, engine: AsyncEngine, bus, registry
) -> None:
    """When a memory.semantic_recall worker is registered, semantic_recall
    is offered and the graph dispatches it through the registry."""

    import asyncio

    from caesar.legion.semantic_recall import CAPABILITY, SemanticRecallWorker
    from caesar.llm.embeddings import StubEmbedder

    audit = AuditLogger(engine)
    worker = SemanticRecallWorker(bus, engine, StubEmbedder(dimension=32))
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
                tool_uses=[ToolUse(id="s1", name="semantic_recall", input={"query": "x"})],
            )
        )
        fake_gateway.queue(
            ChatResponse(
                content="Nothing relevant yet.",
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
                "messages": [ChatMessage(role="user", content="recall fuzzy thing")],
                "decision_id": "d-sem",
                "iteration": 0,
            }
        )

        first_tools = fake_gateway.calls[0]["tools"]
        assert first_tools is not None
        tool_names = [t.name for t in first_tools]
        assert "semantic_recall" in tool_names
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


class _FakeRegistry:
    """Minimal stub satisfying the brain graph's WorkerRegistry contract."""

    def __init__(
        self,
        *,
        capabilities: list[str],
        dispatch_result: dict[str, object] | None = None,
        dispatch_success: bool = True,
        dispatch_error: str | None = None,
    ) -> None:
        from caesar.legion.protocol import WorkerRegistration

        self._capabilities = capabilities
        self._dispatch_result = dispatch_result
        self._dispatch_success = dispatch_success
        self._dispatch_error = dispatch_error
        self._registration = WorkerRegistration(
            worker_id="fake",
            capabilities=capabilities,
            version="0.0.0",
        )
        self.dispatch_calls: list[tuple[str, dict[str, Any]]] = []

    def find(self, capability: str) -> list[Any]:
        return [self._registration] if capability in self._capabilities else []

    async def dispatch(
        self,
        capability: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        decision_id: str | None = None,
    ) -> Any:
        from caesar.legion.protocol import TaskResult

        self.dispatch_calls.append((capability, payload or {}))
        return TaskResult(
            task_id="t",
            worker_id="fake",
            success=self._dispatch_success,
            result=self._dispatch_result if self._dispatch_success else None,
            error=self._dispatch_error,
        )


def _allow_calculator_policy() -> Policy:
    from caesar.policy.yaml_loader import AllowedToolRule

    return AllowlistPolicy(
        RulesConfig(
            version=1,
            allowed_services=[],
            allowed_tools=[AllowedToolRule(tool="calculator")],
        )
    )


async def test_calculator_tool_registered_when_capability_present(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    """The calculator tool only shows up when a worker advertises it."""

    audit = AuditLogger(engine)
    registry = _FakeRegistry(capabilities=["tool.calculator"])
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=None,
        policy=_allow_calculator_policy(),
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="what is 1+1")],
            "decision_id": "d-cap-1",
            "iteration": 0,
        }
    )
    tools = fake_gateway.calls[0]["tools"] or []
    assert any(t.name == "calculator" for t in tools)


async def test_calculator_dispatch_success_path(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    """Allowed call → dispatched, result returned, tool.called audited."""

    from sqlalchemy import desc, select

    from caesar.db.schema import audit_log

    audit = AuditLogger(engine)
    registry = _FakeRegistry(
        capabilities=["tool.calculator"],
        dispatch_result={"expression": "1+1", "value": 2.0},
    )
    fake_gateway.queue(
        ChatResponse(
            content="",
            model="fake-model",
            input_tokens=1,
            output_tokens=2,
            stop_reason="tool_use",
            tool_uses=[
                ToolUse(id="tu_calc", name="calculator", input={"expression": "1+1"}),
            ],
        )
    )
    fake_gateway.queue(
        ChatResponse(
            content="The answer is 2.", model="fake-model", input_tokens=3, output_tokens=4
        )
    )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=None,
        policy=_allow_calculator_policy(),
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="what is 1+1")],
            "decision_id": "d-calc-1",
            "iteration": 0,
        }
    )
    assert registry.dispatch_calls == [("tool.calculator", {"expression": "1+1"})]
    tool_result = fake_gateway.calls[1]["messages"][-1].tool_results[0]
    assert tool_result.is_error is False
    assert "2.0" in tool_result.content or '"value": 2' in tool_result.content

    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    select(audit_log)
                    .where(audit_log.c.event_type == "tool.called")
                    .order_by(desc(audit_log.c.id))
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    assert rows[0]["payload"]["tool"] == "calculator"
    assert rows[0]["payload"]["success"] is True


async def test_calculator_dispatch_denied_by_policy(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    """When the policy denies, the worker is never called and tool.denied lands in the audit log."""

    from sqlalchemy import select

    from caesar.db.schema import audit_log

    audit = AuditLogger(engine)
    registry = _FakeRegistry(
        capabilities=["tool.calculator"],
        dispatch_result={"expression": "1+1", "value": 2.0},
    )
    fake_gateway.queue(
        ChatResponse(
            content="",
            model="fake-model",
            input_tokens=1,
            output_tokens=2,
            stop_reason="tool_use",
            tool_uses=[
                ToolUse(id="tu_calc", name="calculator", input={"expression": "1+1"}),
            ],
        )
    )
    fake_gateway.queue(
        ChatResponse(content="Cannot.", model="fake-model", input_tokens=3, output_tokens=4)
    )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=None,
        policy=DenyAllPolicy(),  # nothing allowed
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="?")],
            "decision_id": "d-calc-2",
            "iteration": 0,
        }
    )
    assert registry.dispatch_calls == []  # worker never asked
    tool_result = fake_gateway.calls[1]["messages"][-1].tool_results[0]
    assert tool_result.is_error is True
    assert "Denied" in tool_result.content

    async with engine.connect() as conn:
        rows = (
            (await conn.execute(select(audit_log).where(audit_log.c.event_type == "tool.denied")))
            .mappings()
            .all()
        )
    assert len(rows) == 1
    assert rows[0]["payload"]["tool"] == "calculator"


async def test_calculator_dispatch_worker_failure(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    """When the worker raises, tool.called(success=False) is logged and the LLM sees the error."""

    from sqlalchemy import select

    from caesar.db.schema import audit_log

    audit = AuditLogger(engine)
    registry = _FakeRegistry(
        capabilities=["tool.calculator"],
        dispatch_success=False,
        dispatch_error="invalid syntax",
    )
    fake_gateway.queue(
        ChatResponse(
            content="",
            model="fake-model",
            input_tokens=1,
            output_tokens=2,
            stop_reason="tool_use",
            tool_uses=[
                ToolUse(id="tu_bad", name="calculator", input={"expression": "1 +"}),
            ],
        )
    )
    fake_gateway.queue(
        ChatResponse(content="Sorry.", model="fake-model", input_tokens=3, output_tokens=4)
    )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=None,
        policy=_allow_calculator_policy(),
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="bad")],
            "decision_id": "d-calc-3",
            "iteration": 0,
        }
    )
    tool_result = fake_gateway.calls[1]["messages"][-1].tool_results[0]
    assert tool_result.is_error is True
    assert "invalid syntax" in tool_result.content
    async with engine.connect() as conn:
        rows = (
            (await conn.execute(select(audit_log).where(audit_log.c.event_type == "tool.called")))
            .mappings()
            .all()
        )
    assert len(rows) == 1
    assert rows[0]["payload"]["success"] is False
    assert rows[0]["payload"]["error"] == "invalid syntax"


async def test_web_search_tool_registered_when_capability_present(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    """web_search shows up in the LLM tool list only when a worker
    advertises tool.web_search."""

    from caesar.policy.yaml_loader import AllowedToolRule

    audit = AuditLogger(engine)
    registry = _FakeRegistry(capabilities=["tool.web_search"])
    policy = AllowlistPolicy(
        RulesConfig(
            version=1,
            allowed_services=[],
            allowed_tools=[AllowedToolRule(tool="web_search")],
        )
    )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=None,
        policy=policy,
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="what's the weather")],
            "decision_id": "d-search-1",
            "iteration": 0,
        }
    )
    tools = fake_gateway.calls[0]["tools"] or []
    assert any(t.name == "web_search" for t in tools)


async def test_web_search_dispatch_success_path(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    """The brain dispatches web_search via _handle_generic_tool and
    surfaces results to the LLM."""

    from caesar.policy.yaml_loader import AllowedToolRule

    audit = AuditLogger(engine)
    registry = _FakeRegistry(
        capabilities=["tool.web_search"],
        dispatch_result={
            "query": "homelab nas",
            "results": [
                {
                    "title": "NAS guide",
                    "url": "https://example.com/nas",
                    "snippet": "...",
                    "domain": "example.com",
                }
            ],
        },
    )
    fake_gateway.queue(
        ChatResponse(
            content="",
            model="fake-model",
            input_tokens=1,
            output_tokens=2,
            stop_reason="tool_use",
            tool_uses=[
                ToolUse(id="tu_ws", name="web_search", input={"query": "homelab nas", "limit": 3}),
            ],
        )
    )
    fake_gateway.queue(
        ChatResponse(content="I found one.", model="fake-model", input_tokens=3, output_tokens=4)
    )
    policy = AllowlistPolicy(
        RulesConfig(
            version=1,
            allowed_services=[],
            allowed_tools=[AllowedToolRule(tool="web_search")],
        )
    )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=None,
        policy=policy,
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="search")],
            "decision_id": "d-search-2",
            "iteration": 0,
        }
    )
    assert registry.dispatch_calls == [
        ("tool.web_search", {"query": "homelab nas", "limit": 3}),
    ]
    tool_result = fake_gateway.calls[1]["messages"][-1].tool_results[0]
    assert tool_result.is_error is False
    assert "example.com" in tool_result.content


async def test_calendar_read_tool_registered_when_capability_present(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    from caesar.policy.yaml_loader import AllowedToolRule

    audit = AuditLogger(engine)
    registry = _FakeRegistry(capabilities=["tool.calendar_read"])
    policy = AllowlistPolicy(
        RulesConfig(
            version=1,
            allowed_services=[],
            allowed_tools=[AllowedToolRule(tool="calendar_read")],
        )
    )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=None,
        policy=policy,
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="what's on my calendar")],
            "decision_id": "d-cal-1",
            "iteration": 0,
        }
    )
    tools = fake_gateway.calls[0]["tools"] or []
    assert any(t.name == "calendar_read" for t in tools)


async def test_calendar_read_dispatch_returns_events_to_llm(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    from caesar.policy.yaml_loader import AllowedToolRule

    audit = AuditLogger(engine)
    registry = _FakeRegistry(
        capabilities=["tool.calendar_read"],
        dispatch_result={
            "from": "2026-05-17T00:00:00+00:00",
            "to": "2026-05-24T00:00:00+00:00",
            "events": [
                {
                    "title": "Standup",
                    "start": "2026-05-17T09:00:00+00:00",
                    "end": "2026-05-17T09:15:00+00:00",
                    "calendar": "Work",
                    "location": "",
                    "description": "",
                }
            ],
        },
    )
    fake_gateway.queue(
        ChatResponse(
            content="",
            model="fake-model",
            input_tokens=1,
            output_tokens=2,
            stop_reason="tool_use",
            tool_uses=[
                ToolUse(
                    id="tu_cal",
                    name="calendar_read",
                    input={"from": "2026-05-17T00:00:00Z"},
                )
            ],
        )
    )
    fake_gateway.queue(
        ChatResponse(content="One thing.", model="fake-model", input_tokens=3, output_tokens=4)
    )
    policy = AllowlistPolicy(
        RulesConfig(
            version=1,
            allowed_services=[],
            allowed_tools=[AllowedToolRule(tool="calendar_read")],
        )
    )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=None,
        policy=policy,
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="agenda?")],
            "decision_id": "d-cal-2",
            "iteration": 0,
        }
    )
    assert registry.dispatch_calls == [
        ("tool.calendar_read", {"from": "2026-05-17T00:00:00Z"}),
    ]
    tool_result = fake_gateway.calls[1]["messages"][-1].tool_results[0]
    assert tool_result.is_error is False
    assert "Standup" in tool_result.content


async def test_notify_tool_registered_when_capability_present(
    fake_gateway: FakeGateway, engine: AsyncEngine
) -> None:
    """notify shows up in the LLM tool list only when a worker advertises
    ``tool.notify`` — the brain doesn't offer it on bare installs."""

    from caesar.policy.yaml_loader import AllowedToolRule

    audit = AuditLogger(engine)
    registry = _FakeRegistry(capabilities=["tool.notify"])
    policy = AllowlistPolicy(
        RulesConfig(
            version=1,
            allowed_services=[],
            allowed_tools=[AllowedToolRule(tool="notify")],
        )
    )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=None,
        policy=policy,
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="say hi")],
            "decision_id": "d-notify-1",
            "iteration": 0,
        }
    )
    tools = fake_gateway.calls[0]["tools"] or []
    assert any(t.name == "notify" for t in tools)


async def test_notify_dispatch_success_path(fake_gateway: FakeGateway, engine: AsyncEngine) -> None:
    """The brain dispatches notify via _handle_generic_tool, the ntfy
    delivery id comes back to the LLM, and the call is audit-logged."""

    from caesar.policy.yaml_loader import AllowedToolRule

    audit = AuditLogger(engine)
    registry = _FakeRegistry(
        capabilities=["tool.notify"],
        dispatch_result={
            "id": "msg-42",
            "delivered_at": "2026-05-17T12:00:00+00:00",
        },
    )
    fake_gateway.queue(
        ChatResponse(
            content="",
            model="fake-model",
            input_tokens=1,
            output_tokens=2,
            stop_reason="tool_use",
            tool_uses=[
                ToolUse(
                    id="tu_notify",
                    name="notify",
                    input={"title": "Morning brief", "message": "All clear."},
                ),
            ],
        )
    )
    fake_gateway.queue(
        ChatResponse(content="Notified.", model="fake-model", input_tokens=3, output_tokens=4)
    )
    policy = AllowlistPolicy(
        RulesConfig(
            version=1,
            allowed_services=[],
            allowed_tools=[AllowedToolRule(tool="notify")],
        )
    )
    graph = build_brain_graph(
        gateway=fake_gateway,
        ha=None,
        policy=policy,
        audit=audit,
        registry=registry,  # type: ignore[arg-type]
    )
    await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="brief me")],
            "decision_id": "d-notify-2",
            "iteration": 0,
        }
    )
    assert registry.dispatch_calls == [
        ("tool.notify", {"title": "Morning brief", "message": "All clear."}),
    ]
    tool_result = fake_gateway.calls[1]["messages"][-1].tool_results[0]
    assert tool_result.is_error is False
    assert "msg-42" in tool_result.content


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
