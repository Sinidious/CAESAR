# 0017 — Configuration via pydantic-settings with layered sources

- Status: Accepted
- Date: 2026-05-16
- Deciders: @sinidious

## Context

Every CAESAR subsystem needs configuration: Praetor needs an HTTP bind
address, the LLM Gateway needs provider credentials and per-agent
model routing ([ADR-0011](0011-llm-gateway.md)), the HA Bridge needs
an HA URL and token ([ADR-0007](0007-home-assistant-bridge.md)), the
bus client needs a NATS URL and NKEY ([ADR-0009](0009-message-bus-nats.md)),
the audit writer needs a SQLite path ([ADR-0012](0012-audit-log.md)).

There is no universe in which a homelab operator wants to edit
hard-coded constants. There are also two distinct populations of
configuration:

1. **Secrets** — API keys, tokens, NKEYs. Never on disk in the repo,
   typically come from environment variables, sometimes from a file
   the operator hand-edits and `chmod 600`s.
2. **Operator preferences** — bind addresses, log levels, per-agent
   model assignments, policy rule paths. These belong in a versioned,
   commented file the operator owns.

Mixing them is the usual sin. So is making the operator stitch them
together themselves.

A third quiet requirement: tests must be able to construct a fully
valid config in-process without touching the filesystem or the
environment.

## Decision

CAESAR uses **`pydantic-settings`** as the configuration framework,
loading from layered sources in a fixed precedence:

1. Explicit constructor arguments (tests, programmatic overrides).
2. Process environment variables (`CAESAR_*`).
3. `.env` file in the working directory (developer secrets;
   gitignored).
4. The operator config file: TOML at `$CAESAR_CONFIG` (default
   `/etc/caesar/caesar.toml` on Linux, `./caesar.toml` in dev).
5. In-code defaults.

Higher-numbered sources lose to lower-numbered ones. Same source as
existing `pydantic-settings` semantics, with the TOML loader added.

Shape:

- **One `caesar.config.Settings` model** at the top, composed of
  nested per-subsystem models (`PraetorSettings`, `GatewaySettings`,
  `BridgeHASettings`, `BusSettings`, `AuditSettings`, `MemorySettings`,
  `PolicySettings`, `LoggingSettings`).
- **Environment variables use nested delimiters**:
  `CAESAR_GATEWAY__ANTHROPIC__API_KEY` maps to
  `settings.gateway.anthropic.api_key`. `__` is the delimiter
  (`pydantic-settings` default), matching the conventional way to flatten
  hierarchies into env.
- **TOML file mirrors the same shape**:
  ```toml
  [praetor]
  host = "127.0.0.1"
  port = 8080

  [gateway.anthropic]
  # api_key intentionally not in the file — set via env or .env.
  default_model = "claude-opus-4-7"
  ```
- **Secrets are typed `pydantic.SecretStr`.** Their `repr` does not
  show the value; logging accidentally prints `**********`. Audit
  records never include them either way.
- **`Settings` is loaded once at process start** and passed through
  FastAPI dependency injection; modules do not call
  `Settings()` on import. This keeps tests free of import-time side
  effects and means a misconfigured field fails the process at startup,
  not at first request.
- **`just config-check`** (added when the config loader lands) loads
  and validates the resolved config and prints the source of each
  field. Useful for debugging "why is the gateway picking up a stale
  model?".

`.env.example` (already present in the repo) becomes the documented
list of every env-var-overridable field, generated from the model and
asserted in CI to stay in sync.

## Alternatives considered

- **Plain `os.getenv` plus a manual dict.** Smallest dependency. Pays
  for itself by the third "wait, that value is set but the wrong
  type" incident. Loses the validation, the schema, the IDE
  completion, and the secret repr handling.
- **Dynaconf.** Strong layering story, but the API encourages
  late-binding (`settings.FOO` looked up on access) which fights with
  strict typing and with our "fail at startup" goal. Heavier than we
  need.
- **Hydra + OmegaConf.** Excellent for ML experiments; the composition
  model is overkill for a service with a fixed config tree and three
  source layers.
- **TOML-only, no env support.** Friendly for operators who hand-edit
  a file; hostile for containers and CI, both of which speak env
  variables natively. Operators can ignore the env layer if they want
  to; containers can't ignore the file layer cleanly.
- **JSON or YAML config file.** TOML is the Python ecosystem's
  trajectory (`pyproject.toml`, `uv`, `ruff`), is comment-friendly,
  and is harder to write a security footgun in than YAML. JSON has
  no comments; for an operator-edited file that is unacceptable.

## Consequences

### Positive

- Every subsystem reads its config from one typed, validated object.
- Tests build `Settings(...)` directly and never need a `.env` file
  or `monkeypatch.setenv`.
- Containers (later) set `CAESAR_*` env vars; bare-metal operators
  edit `caesar.toml`. Both work without code changes.
- Secrets are typed and don't leak into structured logs
  ([ADR-0018](0018-structured-logging.md)) or audit records
  ([ADR-0012](0012-audit-log.md)).
- "Where did this value come from?" has a single, ordered answer.

### Negative

- `pydantic-settings` is one more dependency in the core path, with
  `pydantic` v2 already pulled in transitively by FastAPI.
- The layered model has a learning cost for new contributors. We
  document it once in `docs/CONFIGURATION.md` and link to it from
  errors raised at startup.

### Neutral

- Hot reload of config (re-read on SIGHUP) is **not** part of this
  ADR. v0.x restarts the process. If we later add reload, it's a new
  ADR because some fields (e.g. SQLite path, bus URL) are not safe to
  change without a restart.
- Per-environment files (`caesar.prod.toml`, `caesar.dev.toml`) are
  not part of this ADR. Operators can `$CAESAR_CONFIG=/path/...` per
  process; convention beats config for a homelab.

## References

- [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [pydantic SecretStr](https://docs.pydantic.dev/latest/api/types/#pydantic.types.SecretStr)
- [PEP 680 — `tomllib`](https://peps.python.org/pep-0680/)
- [docs/CONFIGURATION.md](../CONFIGURATION.md)
- [ADR-0011 — Provider-agnostic LLM Gateway](0011-llm-gateway.md)
- [ADR-0018 — Structured logging with structlog](0018-structured-logging.md)
