# CLAUDE.md — Working Notes for Future Claude Code Sessions

This file is the persistent context for Claude Code (and other AI
coding assistants) working on CAESAR. Read it at the start of every
session before touching the code.

## What this project is

CAESAR is a self-hosted homelab AI assistant. The central brain
(**Praetor**) coordinates a pool of pluggable worker agents (**Legion**),
talks to Home Assistant for device control, accepts voice input, drives
a web dashboard, and supports any number of LLMs with per-agent
personalities and task priorities.

## Vocabulary

Two evocative names are intentional; everything else is plain English.

- **Praetor** — the central brain. A Python service (FastAPI +
  LangGraph) that owns intent, memory, and policy.
- **Legion** — the pool of worker agents. Each worker (an agent or a
  tool) registers with Praetor and handles a slice of work.

Plain-English subsystems:

- **Dashboard** — the web UI (and later mobile app).
- **Voice Satellite** — a microphone/speaker endpoint speaking the
  Wyoming protocol.
- **HA Bridge** — the module that talks to Home Assistant over REST + WS.
- **Memory** — episodic (SQLite) + semantic (vector) store.
- **LLM Gateway** — provider-agnostic abstraction over Anthropic,
  OpenAI, Ollama, vLLM, Groq, etc.
- **Audit Log** — every brain decision, replayable.
- **Message Bus** — inter-process messaging (NATS, TBD per ADR-0009).
- **Policy Engine** — guardrails between agents and the real world.

## Project rules

### No app code without an approved ADR

This repo is doc-and-ADR-driven. Before introducing application code in
a new area (a new module, a new external dependency, a new architectural
pattern), there must be an accepted ADR under `docs/adr/`. If the change
is small (bug fix, refactor, test, doc fix), no ADR is needed.

How to add an ADR:

```sh
just adr-new "<short title>"
```

This copies `docs/adr/0000-template.md` to the next number and opens it.
ADRs are written in MADR format (Context · Decision · Consequences).

### Branching

- Develop on a feature branch (`feature/...`, `fix/...`, `docs/...`).
- The Claude Code remote-execution branch is
  `claude/setup-caesar-framework-9KfgU`. Push there from Claude
  sessions; merge into `main` via PR.
- `main` is the trunk. Do not push to `main` directly — branch
  protection requires PRs + approval + green CI.

### Commits

Use [Conventional Commits](https://www.conventionalcommits.org):

```
<type>(<scope>): <short imperative summary>

<optional body>

<optional footer(s)>
```

Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`,
`build`, `ci`, `perf`. Scope is optional (e.g. `praetor`, `legion`,
`bridge/ha`, `docs/adr`).

**Never** include "Generated with Claude" footers or any other
AI-attribution markers in commits, PR descriptions, code comments, or
release notes. Explicit user instruction.

### Releases

Releases are driven by [release-please](https://github.com/googleapis/release-please)
from Conventional Commits on `main`. Don't tag manually.

### Code quality gates

Before pushing, run:

```sh
just check   # = lint + typecheck + test
```

Or individually:

```sh
just lint        # ruff check + ruff format --check
just fmt         # ruff format (writes)
just typecheck   # mypy
just test        # pytest
```

Pre-commit hooks run on every commit. Install once with:

```sh
just setup
```

### Tests that need `nats-server`

The bus + Legion tests (`tests/test_bus.py`, `tests/test_legion_*.py`,
`tests/test_praetor_app.py::test_lifespan_connects_and_disconnects_bus`)
spin up a real `nats-server` subprocess. Tests skip cleanly when the
binary isn't on `PATH`. CI installs it automatically; for full local
coverage, install once:

- macOS: `brew install nats-server`
- Windows: `scoop install nats-server` (or download from
  https://github.com/nats-io/nats-server/releases)
- Linux: download from GitHub releases and put on `PATH`

Without `nats-server`, those tests skip and the coverage gate may fail
locally — CI is the source of truth.

### CI status checks

`main` branch protection requires:

- `lint`
- `typecheck`
- `test (3.11)`
- `test (3.12)`
- `dependency-review`
- `conventional PR title`
- `branch name prefix`
- `cla`

Match these check names exactly when adding or renaming workflows.

### Merge and review flow

The repo is wired so a maintainer only has to **approve** — everything
else is automated:

- All PRs squash-merge into `main` (merge commits and rebase merges are
  disabled at the repo level). The squashed commit subject is the PR
  title, which release-please reads for Conventional Commits.
- The `auto-merge-on-approval` workflow enables GitHub auto-merge as
  soon as any review with state `approved` is submitted, so once the
  required checks turn green the PR merges itself.
- The `dependabot-auto-merge` workflow auto-approves and auto-merges
  Dependabot patch and minor version bumps. Major version bumps and
  security alerts still wait for a maintainer's approval (and then go
  through the same auto-merge-on-approval path).
- release-please's own release PR is never auto-approved; bumping a
  release is a deliberate act.

If a PR is stuck "waiting for review" with auto-merge enabled, it
almost always means a required check is failing or pending — open the
PR's Checks tab.

## Licensing reminders

- **PolyForm Noncommercial 1.0.0** — noncommercial use only.
- Every PR must come from a contributor who signed the [CLA](CLA.md).
- Do not pull in third-party dependencies under licenses incompatible
  with PolyForm-NC (no AGPL/SSPL/GPL transitive dependencies in code
  we ship). Anything beyond MIT/Apache/BSD/ISC/MPL needs an ADR.

## When in doubt

- Prefer editing over creating.
- Prefer one focused PR over a sprawling one.
- Ask the user via `AskUserQuestion` rather than guessing on choices
  that lock in architecture.
- Trust but verify — read the actual diff before reporting work done.
