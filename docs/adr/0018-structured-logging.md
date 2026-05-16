# 0018 — Structured logging with structlog

- Status: Accepted
- Date: 2026-05-16
- Deciders: @sinidious

## Context

The v0.1 roadmap gate ([ROADMAP](../ROADMAP.md)) explicitly names
"structured logging" as a requirement for Praetor's heartbeat. The
reason is the same as for the audit log ([ADR-0012](0012-audit-log.md))
but smaller in scope: when something misbehaves in the middle of the
night, the maintainer needs a log line that says *what happened, in
which conversation, on which satellite, at which graph node* — not
a stringly-typed `f"failed to dispatch: {e}"`.

Three constraints:

1. **The dashboard will subscribe to logs and audit events
   ([ADR-0012](0012-audit-log.md)).** Subscribers parse, they do not
   regex.
2. **Third-party libraries log via stdlib `logging`.** FastAPI, uvicorn,
   `httpx`, `sqlalchemy`, `langgraph`. Our format has to absorb their
   output without losing structure.
3. **Local dev wants colour and brevity; prod wants single-line JSON.**
   The same code has to do both.

There is also a softer requirement, the same as in ADR-0012: trust is
bought with visibility. Logs are the cheap, always-on observability
layer. The audit log is a *deliberate* record; logs are an *automatic*
record. We need both, and they need to share an id so an operator can
pivot from a log line to the audit row that explains the surrounding
decision.

## Decision

CAESAR uses **`structlog`** as the application logging library, with
the stdlib `logging` module bridged into it so third-party logs come
out in the same format. Specifically:

- **`structlog` is the only logger app code calls.** App modules do
  `logger = structlog.get_logger(__name__)` and never
  `logging.getLogger(...)`.
- **Stdlib `logging` is configured to feed `structlog`** via
  `structlog.stdlib.ProcessorFormatter`. Third-party logs (uvicorn,
  FastAPI, httpx) flow through the same pipeline and come out in the
  same JSON shape.
- **Two render modes, one switch.** `LoggingSettings.format`
  ([ADR-0017](0017-configuration.md)) is `"json"` (default) or
  `"console"`. Console mode uses `structlog.dev.ConsoleRenderer` with
  colour; JSON mode uses `structlog.processors.JSONRenderer`. Test
  runs default to console; deployments default to JSON.
- **Standard processor pipeline** (every record carries these fields):
  - `timestamp` — ISO-8601 UTC, added by
    `TimeStamper(fmt="iso", utc=True)`.
  - `level` — lowercase string.
  - `logger` — dotted module name.
  - `event` — the message; short, present-tense.
  - `request_id` — added by FastAPI middleware (see below).
  - `decision_id` — added by the LangGraph node context manager when
    inside a decision; absent outside one.
  - `conversation_id`, `satellite_id` — added by middleware/context
    when the request originates from a conversation or satellite.
  - `exception` — formatted by `format_exc_info` when present.
- **Request id middleware.** A FastAPI middleware reads or generates
  a request id (`X-Request-Id` header, falling back to `uuid4().hex`)
  and binds it via `structlog.contextvars.bind_contextvars` for the
  duration of the request. The id is also echoed in the response
  header so the dashboard and `curl` debugging can correlate.
- **Decision id binding.** A small context manager wraps each
  LangGraph node entry; it binds `decision_id` (and clears it on
  exit), keeping log lines linkable to audit rows
  ([ADR-0012](0012-audit-log.md)).
- **Levels.** `DEBUG` for graph-internal traces, `INFO` for state
  transitions and side effects, `WARNING` for recoverable degradations
  (provider fallover, rate-limit retries), `ERROR` for things that
  needed a human's attention. `CRITICAL` is reserved for "process
  cannot continue".
- **No secrets in logs.** `pydantic.SecretStr` from ADR-0017 already
  helps; the processor pipeline also runs a small
  `redact_known_secret_keys` processor as defense in depth, stripping
  values for known keys like `api_key`, `token`, `authorization`,
  `password`.

Logging is configured once at process start, in
`caesar.logging.configure_logging(settings.logging)`. App modules and
tests do not configure logging themselves.

## Alternatives considered

- **Stdlib `logging` alone, JSON-formatted via `python-json-logger`.**
  Works, but every "add a field" turns into
  `logger.info("...", extra={...})` boilerplate and the contextvars
  story is hand-rolled. `structlog` makes structured fields the
  ergonomic default instead of the ceremonial path.
- **`loguru`.** Lovely API, but its formatter is line-string-first;
  adopting it for structured JSON means fighting it. Also: stdlib
  bridging is awkward, which violates our "third-party libs log
  through the same pipeline" requirement.
- **OpenTelemetry logs from day one.** Right answer eventually, wrong
  answer today. OTLP needs a collector running, which is more daemon
  than v0.1 deserves. `structlog`'s JSON shape is easy to feed into
  OTel later by swapping the renderer.
- **Just JSON, no console mode.** Painful to read in `tail -f`
  during development. The cost of the second renderer is one boolean
  in config; the value is one less reason to dread `just dev`.

## Consequences

### Positive

- One pipeline for app and third-party logs; one shape for the
  dashboard to parse.
- Every log line emitted inside a decision is automatically tagged
  with `decision_id`, so logs and the audit log
  ([ADR-0012](0012-audit-log.md)) pivot through one another.
- Local development stays readable; prod stays parseable.
- Secrets have two layers of protection (typed `SecretStr` plus the
  redaction processor).

### Negative

- `structlog` is a new dependency for new contributors to learn. The
  surface they actually touch is small (`get_logger`, `log.info(event,
  field=value)`).
- The bridging configuration is the kind of code that's tedious to
  test and easy to break by accident. We add a small smoke test that
  asserts a third-party stdlib log line arrives in JSON form.

### Neutral

- Log file rotation, shipping to a remote sink, and retention are
  out of scope here. v0.x logs to stdout/stderr; an operator running
  under systemd / Docker gets rotation from the runtime.
- Sampling (rate-limiting noisy `DEBUG` lines) is not part of this
  ADR. If we need it, we add a processor and document it.

## References

- [structlog](https://www.structlog.org/)
- [structlog + stdlib logging integration](https://www.structlog.org/en/stable/standard-library.html)
- [structlog contextvars](https://www.structlog.org/en/stable/contextvars.html)
- [ADR-0012 — Audit every brain decision](0012-audit-log.md)
- [ADR-0017 — Configuration via pydantic-settings](0017-configuration.md)
