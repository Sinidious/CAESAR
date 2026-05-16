# 0016 — Repository and package layout

- Status: Accepted
- Date: 2026-05-16
- Deciders: @sinidious

## Context

v0.1 ([ROADMAP](../ROADMAP.md)) is the first time CAESAR ships
application code. Before any module lands, we need to decide what the
import paths look like, where files live, and how multiple subsystems
(Praetor, Legion workers, the HA Bridge, the LLM Gateway, the
dashboard's backend) coexist in one repository.

The cost of getting this wrong is silent and slow: every later module
inherits the choice, every refactor that fixes it touches every file,
and `from caesar.x.y import z` either reads like the architecture
([ADR-0006](0006-praetor-runtime.md), [ADR-0011](0011-llm-gateway.md))
or it doesn't.

Three forcing functions shape the answer:

1. CAESAR is a single project shipped by a single maintainer. A
   monorepo is right; a multi-repo split is premature.
2. Praetor and the workers will run as separate processes, but they
   share a non-trivial amount of code (config loading, logging, the
   message bus client, the LLM gateway interface, audit shapes).
3. The toolchain ([ADR-0015](0015-python-toolchain.md)) is
   `hatchling` + `ruff` + `mypy` strict + `pytest`. `mypy --strict`
   on a flat layout is painful; a `src/` layout makes editable installs
   and import isolation behave.

## Decision

CAESAR is a **single Python package** named `caesar`, in a **`src/`
layout**, shipping as **one wheel**. Subpackages mirror the
architecture:

```
src/
  caesar/
    __init__.py
    config/          # ADR-0017 — pydantic-settings models, file loader.
    logging/         # ADR-0018 — structlog setup, request-id middleware.
    audit/           # ADR-0012 — schemas + writer.
    memory/          # ADR-0010 — episodic + semantic.
    gateway/         # ADR-0011 — LLM gateway interface + providers.
    policy/          # ADR-0013 — policy engine.
    bus/             # ADR-0009 — NATS client wrapper.
    bridge/
      ha/            # ADR-0007 — Home Assistant client.
    praetor/         # ADR-0006 — FastAPI app + LangGraph graph.
      api/           # routers, dependencies, error handlers.
      graph/         # LangGraph nodes and edges.
      app.py         # FastAPI app factory.
      __main__.py    # `python -m caesar.praetor` entry point.
    legion/
      _base/         # worker SDK: registration, dispatch loop, types.
      memory/        # first worker (planned v0.3+).
      ...            # future workers, one subpackage each.
tests/
  unit/              # mirrors src/caesar/ tree.
  integration/       # cross-module + bus + sqlite.
docs/
  adr/
  ...
```

Rules:

- **One namespace, `caesar`.** Everything imports as `from caesar.x
  import Y`. No `caesar_praetor`, no top-level `praetor`, no
  `from praetor.app import ...`.
- **`src/` layout.** Editable installs (`pip install -e .`) must not
  shadow installed packages from the working directory. `mypy
  --strict` resolves modules predictably under `src/`.
- **One wheel, multiple entry points.** `pyproject.toml` declares
  console scripts for each runnable subsystem:
  `caesar-praetor`, `caesar-legion-<name>` (later). Workers and Praetor
  ship together; an operator running only one still has the others on
  disk but doesn't run them.
- **Subpackage = subsystem.** A new subsystem is a new top-level
  subpackage. A new module *inside* an existing subsystem is not an
  ADR-worthy change; a new subsystem is (per [CLAUDE.md](../../CLAUDE.md)).
- **`tests/` mirrors `src/caesar/`.** `tests/unit/praetor/test_app.py`
  tests `caesar.praetor.app`. Integration tests live under
  `tests/integration/` and are not required to mirror source.
- **No circular dependencies between subpackages.** A topological order
  is implied by the architecture: `config` and `logging` depend on
  nothing internal; `audit`, `bus`, `gateway`, `memory`, `policy`
  depend on those two; `bridge`, `praetor`, `legion` depend on the
  layer above. Enforced by review, not by tooling, until it breaks.
- **`caesar.legion._base` is private API for now.** Workers in this
  repo import it directly; a future external-worker SDK will be
  promoted to `caesar.legion.sdk` with its own ADR.

`pyproject.toml` changes that fall out of this ADR (made when the
first module lands, not here):

- `[tool.hatch.build.targets.wheel] packages = ["src/caesar"]` and
  remove `bypass-selection`.
- `[tool.mypy] files = ["src", "tests"]`.
- `[tool.coverage.run] source = ["src/caesar", "tests"]`.
- `[tool.ruff] src = ["src", "tests"]` so isort groups first-party
  imports correctly.

## Alternatives considered

- **Flat layout (`caesar/` at repo root).** Simpler today, fragile
  later: editable installs pick up the working-directory copy ahead of
  the installed one, `mypy --strict` produces "found module X but no
  type information" surprises, and `pytest` import modes interact
  badly. The cost of `src/` is one directory level; the cost of flat
  is paid every time someone runs the test suite oddly.
- **Multi-package monorepo (`packages/praetor`, `packages/legion-*`,
  shared `packages/core`).** Real workspaces buy something when teams
  diverge or when one component publishes independently. We have one
  maintainer and one release stream ([ADR-0004](0004-conventional-commits-and-release-please.md));
  workspaces are friction without a payoff.
- **Multi-repo (one repo per subsystem).** Worst of both: ADRs scatter,
  CI multiplies, the audit story breaks across boundaries that don't
  match a real conceptual split. Reserved for the day a subsystem
  genuinely wants its own release cadence.
- **Top-level modules named after subsystems (`praetor/`, `legion/`,
  `bridge/`).** Pollutes the import namespace and makes "is this
  CAESAR or some third-party `praetor` package?" a real question.
  Reserving the `caesar.` prefix costs nothing.

## Consequences

### Positive

- One `pip install caesar` puts everything an operator might run on
  disk; choice of which process to start is a config / systemd-unit
  question, not a packaging question.
- `from caesar.gateway import LLMGateway` reads like the architecture.
- Shared modules (config, logging, audit shapes, bus client) have an
  obvious home and don't accumulate copies.
- The `src/` layout makes `mypy --strict` and editable installs behave
  the same in dev as in CI.

### Negative

- A new contributor working only on Legion still clones the whole
  repo. That's the same tradeoff every monorepo makes; we accept it.
- "One wheel, many entry points" means an operator who runs only the
  HA Bridge still has Praetor's dependencies in their env. Acceptable
  for v0.x; revisit with `optional-dependencies` groups if v1.0 ships
  to operators who want a minimal install.

### Neutral

- The dashboard's frontend is **not** in this layout — it will live
  under `apps/dashboard/` (or similar) when it lands, with its own
  ADR. The dashboard's *backend* is just a FastAPI router under
  `caesar.praetor.api`.
- Anything published outside Python (a future TypeScript SDK, a Helm
  chart) lives at the repo root, not under `src/`. No ADR needed for
  that until it exists.

## References

- [Python Packaging User Guide — src layout vs flat layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
- [hatchling — Build configuration](https://hatch.pypa.io/latest/config/build/)
- [ADR-0006 — Praetor on FastAPI + LangGraph](0006-praetor-runtime.md)
- [ADR-0011 — Provider-agnostic LLM Gateway](0011-llm-gateway.md)
- [ADR-0015 — Python toolchain](0015-python-toolchain.md)
