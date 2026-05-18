# Roadmap

CAESAR ships in small, demonstrable slices. Each milestone has a single
"can it do this?" question; if the answer is no, we don't move on.

> This is a personal homelab project. Dates are aspirational, scope is
> not. Slip the date, not the gate.

## v0.0 — Framework (now)

**Question:** Can a contributor clone the repo, run `just check`, and
see what we're building?

- [x] License (PolyForm-NC), CLA, contributor docs.
- [x] CI: lint, typecheck, test (3.11 + 3.12), CLA Assistant Lite.
- [x] Conventional Commits + release-please wired to `main`.
- [x] First batch of architecture ADRs.
- [x] Branch protection enabled on `main` (rulesets + auto-merge flow).

## v0.1 — Praetor heartbeat

**Question:** Does the brain start up and answer a "hello" over HTTP?

- FastAPI skeleton, health endpoint, structured logging, audit-log
  schema (write-only stub).
- LangGraph "echo" state machine: take text in, return text out.
- LLM Gateway with a single Anthropic provider behind it.

## v0.2 — Speak to the house

**Question:** Can I say "turn on the kitchen light" (from my phone's
dictation, or any HTTP client) and have it happen?

- HA Bridge (REST + WS, single token).
- LLM tool-use in `/v1/chat` that lets the brain call HA services
  through the Policy Engine (no dedicated voice-satellite hardware;
  see ADR-0008 for the rewrite away from Wyoming).
- Policy Engine with at least one rule loaded from YAML.

## v0.3 — Legion of one

**Question:** Can a worker register with Praetor over NATS, be picked
for a task, and return a result?

- NATS bus, worker registration protocol.
- First Legion worker: a memory-recall worker that reads from SQLite.

## v0.4 — Memory that sticks

**Question:** Does CAESAR remember yesterday?

- Episodic memory (SQLite) with retention policy.
- Semantic memory (vector store) with a first retrieval worker.
- Memory writes are audit-logged.

## v0.5 — Dashboard

**Question:** Can I see what Praetor decided and why, in a browser?

- Web dashboard: live audit log, intent timeline, agent activity.
- Per-agent personality + priority config UI.

## v1.0 — Daily-driver ready

**Question:** Would I trust this to run my house unattended for a week?

- [x] All v0.x gates passing.
- [x] Stability + observability: Prometheus `/metrics` + OpenTelemetry tracing.
- [x] Documented backup/restore (ADR-0022).
- [x] Security review of the policy engine ([SECURITY-REVIEW.md](SECURITY-REVIEW.md), ADR-0025).
- [x] Public docs site (mkdocs-material, ADR-0024).

## v1.1 — Provider flexibility

**Question:** Can I run CAESAR on the model I picked — including a
fully-local one — without forking the brain?

The LLM Gateway has been provider-agnostic since
[ADR-0011](adr/0011-llm-gateway.md), but only Anthropic is wired.
v1.1 fills it in so an operator can choose per-task between
Anthropic, OpenAI, and a local Ollama — with full tool-calling on
each. ADR-0026 covers the design.

- [x] ADR-0026: multi-provider gateway design (tool-call
      normalisation across Anthropic / OpenAI / Ollama shapes;
      per-task routing config; reasoning-token accounting).
- [x] OpenAI provider — covers GPT and Azure-OpenAI; native
      function-calling translated to our `ToolUse` / `ToolResult`.
- [x] Ollama provider — fully-local operation; tool calling via
      the Ollama 0.4+ tool API.
- [x] Per-task routing config — operator can assign different
      models to `/v1/chat`, `memory_recall`, `semantic_recall`,
      etc. without code changes.
- [x] Docs: ["How to pick a model"](PICKING-A-MODEL.md) page with
      cost/latency/privacy trade-offs.

## v1.2 — Legion across hosts

**Question:** Can a worker run on a different box and CAESAR still
treats it like a first-class member of the pool?

The Legion bus has been NATS since [ADR-0009](adr/0009-message-bus-nats.md)
but v0.3 onward shipped single-node localhost only; no auth, no TLS,
no story for "worker on the GPU box, brain on the NUC". v1.2 makes
that real with NKEY-per-identity NATS auth, scoped subject
permissions, and an opt-in path that preserves the existing
single-host posture for operators who don't want to deal with it.
ADR-0027 covers the design (and closes SR-009 in the process).

- [ ] ADR-0027: NKEY-per-identity NATS auth; subject scoping per
      identity; TLS-optional posture; opt-in `CAESAR_BUS__AUTH__*`
      env vars; backward-compat with current no-auth deployments.
- [ ] Configurable NATS auth in the `caesar.bus.client.Bus`
      wrapper: NKEY seed + JWT or static credentials file.
- [ ] Per-identity subject permissions documented (operator-curated
      nats-server.conf snippet shipped under `examples/`).
- [ ] Worker bootstrap script / docs: how to provision a new
      worker on a fresh machine.
- [x] End-to-end test: two-process worker registers and answers a
      dispatch over an authed bus. Gated when nats-server is on
      `PATH` (same pattern as the existing bus tests).
- [ ] Docs: "Run a worker on another box" page.

## v1.3 — Tools beyond HA

**Question:** Can the brain do something other than turn on lights?

Through v1.2 the only "tools" the brain knew about were
`call_service` (HA), `recall_memory`, and `semantic_recall`. v1.3
widens the toolbox so CAESAR feels like an assistant, not just a
voice-controlled remote. ADR-0028 covers the design.

New tools ship as **Legion workers** (reusing the v1.2 multi-host
arch) and are gated by a **generalised Policy Engine** that
evaluates any tool call by id + input — not just HA service calls.
Existing HA policy keeps working unchanged because `call_service`
becomes one tool id among many.

- [ ] ADR-0028: tool worker shape + Policy Engine generalisation
      + per-tool YAML grammar.
- [ ] Policy generalisation: `evaluate(call)` accepts a uniform
      `ToolCall` shape; existing `ServiceCall` becomes a subtype;
      audit row carries the tool id.
- [ ] Calculator worker: pure-Python, no network, no creds.
      Smallest possible end-to-end exercise of the new tool path.
- [ ] Web-search worker: network call, requires a credentialed
      backend (SearXNG self-hosted *or* a Brave / Tavily API key).
      Policy-gated by allowed-domains list.
- [ ] Calendar-read worker: CalDAV against a homelab calendar.
- [ ] Docs: "Add your own tool" page covering the worker SDK
      shape, policy grammar, audit-row naming conventions.

## v1.4 — Install in 10 minutes

**Question:** Can someone who isn't me deploy CAESAR on a fresh
NUC and have it running in 10 minutes?

v1.0–v1.3 made CAESAR functionally rich. v1.4 makes it *adoptable*:
a published Docker image, a reference Compose stack, and a
`caesar init` command that generates a self-contained working
config without making the operator hand-edit YAML. The bare-metal
pip path keeps working for operators who want it. ADR-0029 covers
the design.

- [ ] ADR-0029: Docker + Compose distribution, `caesar init`
      config-only generator, ghcr.io image naming, multi-arch
      (amd64 + arm64), backward-compat with pip install.
- [ ] `caesar init` command: writes `.env`, `policy.yaml`, a fresh
      dashboard token, a Praetor NKEY (for the multi-host upgrade
      path), and `./var/`. Idempotent; refuses to overwrite an
      existing config without `--force`.
- [ ] `Dockerfile` (multi-stage; runtime image is Python 3.11-slim
      based; bakes the full default install including caldav and
      both LLM SDKs).
- [ ] `docker-compose.yml` reference stack: CAESAR + nats-server
      with commented Ollama + SearXNG services for the
      privacy-conscious operator.
- [ ] GitHub Actions: build + push multi-arch image to
      `ghcr.io/sinidious/caesar` on every release; the existing
      Sigstore provenance attestation (SR-011) covers the image.
- [ ] Docs: "Quickstart in 10 minutes" page wiring `caesar init`
      → `docker compose up` → "open the dashboard" end-to-end.

## Out of scope (for now)

- Mobile native apps (the dashboard will be installable PWA first).
- Multi-tenant operation. CAESAR is for one household.
- Cloud-hosted CAESAR. Self-hosted only by design.
