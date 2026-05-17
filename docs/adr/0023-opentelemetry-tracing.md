# 0023 — OpenTelemetry tracing as an opt-in extra

- Status: Accepted
- Date: 2026-05-17
- Deciders: @Sinidious
- Related issues / PRs: v1.0 observability slice (after [ADR-0018](0018-structured-logging.md))

## Context

Prometheus metrics now cover counters, histograms, and sampled gauges,
but they answer *what* is slow — not *where*. A single `/v1/chat`
request walks the LangGraph brain, fans out to Anthropic, hits
SQLAlchemy multiple times, and may dispatch to a Legion worker. When a
p95 spike shows up in `caesar_chat_duration_seconds`, the operator
currently has to reason about it from structured logs alone.

OpenTelemetry is the de-facto industry standard for distributed tracing
and pairs naturally with the metrics we just shipped. The forcing
question is *how invasive* the integration should be — many homelab
users have no trace collector and don't want a 30+MB dependency tail
just so the import line resolves.

## Decision

CAESAR will support OpenTelemetry tracing as an **opt-in extra**:
`pip install caesar[otel]`. The default install stays lean; tracing
is a no-op when the SDK is absent.

When the extra is installed and `OTEL_SDK_ENABLED` is not `false`,
Praetor will instrument:

- **HTTP** — `opentelemetry-instrumentation-fastapi` on every request
  to `/v1/chat`, `/v1/audit/stream`, dashboard routes, etc.
- **DB** — `opentelemetry-instrumentation-sqlalchemy` on the async
  engine, covering audit writes, memory reads, settings upserts.
- **LLM** — a custom span around every Anthropic SDK call in the LLM
  Gateway, with `gen_ai.system`, `gen_ai.request.model`, and
  `gen_ai.usage.input_tokens` / `output_tokens` attributes following
  the OTel GenAI semantic conventions.
- **Brain graph** — a custom span per LangGraph node so the operator
  can see which step in the brain took the time.

Default sampler: **ParentBased(AlwaysOn)**. Homelab volumes are
single-digit QPS; full sampling is fine and trims a footgun where an
operator misses sparse traces because the default rate is too low.
Incoming W3C trace context is honoured.

Default exporter: **OTLP/HTTP** to `http://localhost:4318` (Jaeger,
Tempo, SigNoz, Grafana Alloy, etc. all accept it on that port). All
standard `OTEL_*` env vars are passed straight through — we add no
CAESAR-specific knobs beyond a single `tracing.enabled` toggle in
settings to short-circuit the import path.

The setup lives in `caesar/tracing.py` with a single
`setup_tracing(app, settings)` entry point called from
`create_app`. If the `[otel]` extra is missing, the module raises
`ImportError` on call; `create_app` catches that and logs a single
`tracing.disabled` debug line. No spans, no overhead.

## Alternatives considered

- **Hard dependency, env-gated** — Always ship the OTel SDK +
  instrumentations and gate emission on
  `OTEL_TRACES_ENABLED`. Rejected: pulls ~30MB of transitive deps
  into every homelab install even when nothing is collecting traces,
  and makes `caesar` heavier than it needs to be for the README's
  "self-hosted on a NUC" target.
- **Auto-instrumentation via `opentelemetry-distro`** —
  `opentelemetry-bootstrap` injects instrumentations at import time.
  Rejected: surprising behaviour, harder to debug, and conflicts with
  the explicit-imports style elsewhere in CAESAR.
- **HTTP-only / DB-only scope** — Cheap to maintain but misses the
  most useful spans for the brain graph and LLM calls, which is where
  latency actually lives. Rejected as not worth doing.
- **Sample at 10%** — Reduces span volume in front of public
  endpoints, but obscures sparse incidents at homelab QPS. Rejected
  in favour of AlwaysOn; an operator can still override with
  `OTEL_TRACES_SAMPLER=parentbased_traceidratio` plus
  `OTEL_TRACES_SAMPLER_ARG=0.1`.
- **Do nothing** — Operators continue to grep structlog JSON. Workable
  for now, but doesn't scale past the first multi-step brain regression.

## Consequences

### Positive

- Every `/v1/chat` request becomes a navigable trace: HTTP →
  brain-node spans → Anthropic span → DB spans, with timing.
- Pairs cleanly with Prometheus metrics already shipped
  ([ADR-0018](0018-structured-logging.md) for logs,
  metrics endpoint for SLOs, traces for *why*).
- Zero install-time cost for users who don't need it.

### Negative

- One more code path that's only exercised when the extra is
  installed. Tests cover both halves (`InMemorySpanExporter` when the
  extra is present, no-op path when imports are stubbed).
- Custom spans in the brain graph are a small recurring maintenance
  cost — new LangGraph nodes have to either inherit a generic wrapper
  or be wrapped explicitly.

### Neutral

- We follow OTel's GenAI semantic conventions where they exist; they
  are stable enough as of OTel SDK 1.30+. If they change, the cost is
  renaming attributes on the LLM span.
- The dashboard does not display traces. Operators point Jaeger /
  Tempo / Grafana at the OTLP endpoint themselves.

## References

- [OpenTelemetry Python SDK](https://opentelemetry.io/docs/languages/python/)
- [GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)
- [ADR-0018](0018-structured-logging.md) — sibling observability decision.
