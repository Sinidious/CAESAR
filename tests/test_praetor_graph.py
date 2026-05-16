from __future__ import annotations

from caesar.llm.gateway import ChatMessage
from caesar.praetor.graph import build_echo_graph
from tests.conftest import FakeGateway


async def test_graph_invokes_gateway_and_returns_response(
    fake_gateway: FakeGateway,
):
    graph = build_echo_graph(fake_gateway)
    out = await graph.ainvoke(
        {
            "messages": [ChatMessage(role="user", content="hi")],
            "system": "You are CAESAR.",
            "model": "fake-model",
            "decision_id": "d-test",
        }
    )
    assert out["response"].content == "hello back"
    assert fake_gateway.calls[0]["system"] == "You are CAESAR."
    assert fake_gateway.calls[0]["model"] == "fake-model"
