# 0006 — Praetor on FastAPI + LangGraph

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

Praetor — the central brain — has to:

- Serve HTTP and WebSocket endpoints for the dashboard and the HA
  Bridge.
- Drive a deterministic, inspectable conversation/agent flow.
- Persist intermediate state so we can audit and replay decisions
  ([ADR-0012](0012-audit-log.md)).
- Stay readable to a maintainer who is not a full-time platform
  engineer.

Hand-rolling an asyncio event loop with a custom state machine is
plausible but throws away years of community work on agent frameworks.
LangGraph in particular models agent runtimes as explicit, typed
graphs of nodes — a natural fit for an audit-friendly orchestrator.

## Decision

Praetor is a **Python service built on FastAPI for the HTTP/WS surface
and LangGraph for the orchestration graph.** Specifically:

- **FastAPI** owns request lifecycle, auth, and OpenAPI generation.
- **LangGraph** owns the agent state machine. Each user turn enters a
  graph with explicit nodes (intent classification, policy check,
  worker dispatch, response synthesis) and edges that can branch on
  state.
- LangGraph's checkpointer writes to SQLite, the same database that
  backs the audit log. This gives us replay and inspection without
  building a parallel persistence layer.
- The LLM Gateway ([ADR-0011](0011-llm-gateway.md)) sits between
  LangGraph nodes and any provider; nodes never import provider SDKs
  directly.

## Alternatives considered

- **LangChain alone** — too unstable an API surface; LangGraph's
  graph-of-state model is the part we actually want.
- **CrewAI / AutoGen** — opinionated multi-agent orchestrators; the
  opinion ("agents talk to agents in natural language") works against
  auditability.
- **A hand-written asyncio state machine** — fewest dependencies,
  highest control. Rejected because we'd reinvent checkpointing,
  serialization, and the node abstraction.
- **Temporal / Prefect** — overkill for a homelab single-node service;
  good if Praetor ever needs multi-node durable workflows.

## Consequences

### Positive

- Every conversation has a typed graph, a persisted state, and a
  replayable history.
- FastAPI's dependency injection and OpenAPI generation cut a lot of
  boilerplate.
- LangGraph's node concept maps almost 1:1 to "things we want to
  audit and policy-check."

### Negative

- LangGraph is young; APIs may break between minor versions. We pin
  carefully and treat updates as PRs, not background work.
- Anyone learning the codebase has to learn LangGraph first.

### Neutral

- Workers ([ADR-0009](0009-message-bus-nats.md)) are *not* LangGraph
  nodes. They are separate processes that LangGraph nodes dispatch
  to over NATS. This keeps the graph small and the workers
  swappable.

## References

- [FastAPI](https://fastapi.tiangolo.com/)
- [LangGraph](https://langchain-ai.github.io/langgraph/)
- [LangGraph checkpointers](https://langchain-ai.github.io/langgraph/concepts/persistence/)
