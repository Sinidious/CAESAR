# 0028 — Tools beyond HA: workers + generalised Policy Engine

- Status: Accepted
- Date: 2026-05-17
- Deciders: @Sinidious
- Related issues / PRs: v1.3 milestone; extends
  [ADR-0009](0009-message-bus-nats.md),
  [ADR-0013](0013-policy-engine.md), and [ADR-0027](0027-nats-auth-multihost-legion.md).

## Context

CAESAR's brain knows three tools today: `call_service` (Home
Assistant), `recall_memory` (audit log), and `semantic_recall`
(vector store). Two of those (recall) already run as Legion
workers; one (`call_service`) is in-process and goes through the
Policy Engine. The brain is technically capable of more, but the
toolbox is empty enough that an operator chatting to CAESAR can't
do much besides toggle a light.

v1.3's gate question — *"can the brain do something other than turn
on lights?"* — needs three things:

1. A repeatable pattern for adding new tools without rewriting the
   brain graph each time.
2. A way to authorise (or deny) tool invocations that aren't HA
   service calls. The current `Policy.evaluate(ServiceCall)`
   signature only handles one shape.
3. At least one or two real tools shipped under the new pattern so
   the architecture isn't pure theory.

## Decision

CAESAR v1.3 will:

- **Ship new tools as Legion workers** rather than in-process
  handlers. Each tool is a worker process (single host today,
  cross-host already enabled by [ADR-0027](0027-nats-auth-multihost-legion.md))
  with its own NKEY identity and dispatch subject
  (`caesar.dispatch.tool.<name>`). Workers register a capability
  (`tool.web_search`, `tool.calculator`, etc.) the brain graph can
  discover.
- **Generalise the Policy Engine** to evaluate a uniform
  `ToolCall` rather than only `ServiceCall`. The existing HA
  allow-list keeps working — `ServiceCall` becomes the
  representation for one tool id (`call_service`) among many.
- Ship **three** representative tools as the first v1.3 cut:
  - `calculator` — pure-Python, in-worker, no network, no creds.
    Smallest possible exercise of the new path.
  - `web_search` — network call to a configurable backend
    (SearXNG self-hosted *or* a Brave / Tavily API key).
    Policy-gated by an allowed-domains list when the result
    surfaces a URL.
  - `calendar_read` — CalDAV read against a homelab calendar.
    Reads only; writes are a follow-up.

### Worker shape

Each tool worker subclasses the existing `caesar.legion.worker.Worker`
base. It declares one or more capabilities (e.g. `tool.web_search`)
that the brain's `WorkerRegistry` discovers. Dispatch payload:

```json
{
  "tool": "web_search",
  "input": { "query": "...", "limit": 5 },
  "decision_id": "..."
}
```

Reply payload follows the existing `WorkerReply` shape
(`success`, `result`, `error`) so the brain graph's tool-result
handling is one code path.

### Policy generalisation

The `Policy` Protocol grows to:

```python
class Policy(Protocol):
    def evaluate(self, call: ToolCall) -> PolicyDecision: ...
```

where `ToolCall` is a discriminated union of `ServiceCall`
(existing) and `GenericToolCall(tool, input)`. The discriminator is
the `tool` id (or `domain.service` for the existing HA shape).

YAML grammar extends — `allowed_services` keeps its current shape
*and* a new `allowed_tools` block lists per-tool rules:

```yaml
version: 1
allowed_services:
  - light.turn_on
  - service: light.turn_off
    target:
      entity_id: [light.kitchen]
allowed_tools:
  - tool: calculator                      # bare: fully permissive
  - tool: web_search
    input:
      domain_allowlist:
        - en.wikipedia.org
        - search.example.com
```

`AllowlistPolicy.evaluate(call)` switches on the tool id:

- For `call_service` calls, the existing `allowed_services` matcher
  runs (parameter-level constraints from SR-005 still apply).
- For other tool ids, the new `allowed_tools` matcher runs.
  Constraint shape per tool is tool-specific — the YAML parser
  passes the `input:` block through as a `dict[str, Any]` and the
  matcher for that tool decides what each key means.
- An unlisted tool is denied with a `tool X is not on the
  allow-list` reason, mirroring the existing service behaviour.

Backward compatibility: a policy YAML with no `allowed_tools` block
keeps all current behaviour. Operators who don't add new tools see
no change. Operators who *do* add a new tool worker but forget the
policy entry get a `denied` reason at the first call — same
discoverability as forgetting an `allowed_services` line today.

### Audit log

Every tool call lands in the audit log with `event_type =
"tool.called"` (or `"tool.denied"`), payload carrying the tool id,
the input dict (clamped per SR-008), and the resulting
`ToolDecision`. The existing `service.called` event keeps its name
for backward compatibility — it remains the canonical HA shape.

## Alternatives considered

- **In-process tool handlers.** A Python function per tool in the
  brain graph. Simpler boilerplate per tool. Rejected: gives up the
  v1.2 cross-host story without a real benefit; tools that need
  credentials (web search, calendar) shouldn't run in the brain
  process; and the symmetry with `recall_memory` is worth keeping.
- **Hybrid: cheap tools in-process, network tools as workers.**
  Practical but bifurcates the brain graph's dispatch shape.
  Rejected because the integration cost of two shapes outweighs the
  saved overhead of one extra subprocess for `calculator`.
- **Keep policy HA-specific; new tools "always allowed" by being
  enabled.** Simpler. Rejected because v1.3 doubles the surface
  area of what the brain can do; gating only HA leaves the rest
  uncovered. The Policy Engine is the right home for the answer to
  "is this brain allowed to do that?".
- **External tool ecosystem (MCP, LangChain agents).** CAESAR
  could wrap the [Model Context Protocol](https://modelcontextprotocol.io/)
  and let the operator pick from existing servers. Considered for a
  future ADR; for v1.3 we keep tools first-class so they share the
  audit log, policy engine, and Legion auth that already exist.

## Consequences

### Positive

- Adding a new tool becomes "write a worker, add a policy line" —
  no brain-graph changes per tool.
- v1.2's multi-host design pays off: an operator can run heavy
  tools (browser-headless web fetch, ML embeddings) on a GPU box
  while Praetor stays on the NUC.
- The Policy Engine becomes the single source of "what is CAESAR
  allowed to do?" — operators have one file to read.
- `ServiceCall` doesn't go away; it stays the canonical HA shape
  and slots into the new uniform call type as the first concrete
  case.

### Negative

- `Policy.evaluate` signature changes (existing in-process callers
  must update). Mitigated by adding a transitional adapter so
  pre-v1.3 callers keep working through v1.x.
- Each new tool worker is a separate process — operationally one
  more thing for the operator to start. Mitigated by docs +
  per-tool `caesar legion serve-tool <name>` shortcuts.
- Tool input schemas live in two places (the `ToolDefinition` the
  brain hands to the LLM, and the worker's handler). Keeping them
  in sync is convention, not enforced. Acceptable for v1.3; a
  pydantic-derived schema generator is a follow-up.

### Neutral

- Calendar / mail / web-search workers need credentials. Per-tool
  env subgroups (`CAESAR_TOOLS__WEB_SEARCH__BRAVE_API_KEY`,
  `CAESAR_TOOLS__CALENDAR__CALDAV_URL`) follow the same nested-key
  pattern v1.1 introduced for the LLM Gateway.
- The brain graph's `MAX_ITERATIONS_DEFAULT` cap (5) still
  applies. A misbehaving LLM that chains 10 tool calls hits the
  cap; the existing audit + log surfaces what happened.

## References

- [ADR-0006](0006-praetor-runtime.md) — brain graph design.
- [ADR-0009](0009-message-bus-nats.md) — bus choice.
- [ADR-0013](0013-policy-engine.md) — original policy engine.
- [ADR-0027](0027-nats-auth-multihost-legion.md) — multi-host auth
  this ADR builds on.
- [SR-008](../SECURITY-REVIEW.md) — audit payload clamp, applies
  to tool inputs/outputs too.
