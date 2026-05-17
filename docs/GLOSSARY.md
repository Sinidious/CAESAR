# Glossary

The project uses two evocative names — **Praetor** and **Legion** —
because they're easier to talk about than "central orchestrator
service" and "worker agent pool". Everything else is plain English.

## A–Z

**ADR** — Architecture Decision Record. Short markdown files under
[`docs/adr/`](adr/README.md) recording *why* we made a non-trivial
technical choice. New application code in a new area requires an
accepted ADR first (see [CLAUDE.md](https://github.com/Sinidious/CAESAR/blob/main/CLAUDE.md)).

**Audit Log** — Append-only record of every decision Praetor makes,
along with the inputs that produced it. Designed to be replayable. See
[ADR-0012](adr/0012-audit-log.md).

**CLA** — Contributor License Agreement. Every contributor signs the
[CLA](https://github.com/Sinidious/CAESAR/blob/main/CLA.md) before their first PR is merged; this is enforced by
the CLA Assistant Lite workflow.

**Dashboard** — The web UI for CAESAR. Shows the live audit log, intent
timeline, and per-agent personality/priority configuration.

**HA Bridge** — The module that talks to Home Assistant over REST + WS.
Single, well-defined surface so the rest of CAESAR doesn't depend on HA
internals. See [ADR-0007](adr/0007-home-assistant-bridge.md).

**Intent** — A normalized representation of what the user wants
(e.g. `lights.set { area: kitchen, level: 30 }`). Praetor classifies
free-form input into intents before dispatching to workers.

**Legion** — The pool of worker agents. Each worker handles a slice of
work (RAG, tools, memory recall, etc.) and registers with Praetor over
the message bus. Workers are isolated processes.

**LLM Gateway** — Provider-agnostic abstraction over Anthropic, OpenAI,
Ollama, vLLM, Groq, and others. Per-agent personality and task
priorities are configured here. See [ADR-0011](adr/0011-llm-gateway.md).

**MADR** — Markdown Architecture Decision Records. The format CAESAR
ADRs follow (Context · Decision · Consequences). See
[ADR-0001](adr/0001-record-architecture-decisions.md).

**Memory** — Two layers: *episodic* (SQLite, structured) and *semantic*
(vector store). See [ADR-0010](adr/0010-memory-hybrid.md).

**Message Bus** — Inter-process messaging fabric. NATS, per
[ADR-0009](adr/0009-message-bus-nats.md). Subjects are namespaced:
`praetor.*`, `legion.<worker>.*`, `bridge.ha.*`, `audit.*`.

**Policy Engine** — Gatekeeper between agents and the real world.
Declarative rules (YAML) decide what side effects are allowed in
context. See [ADR-0013](adr/0013-policy-engine.md).

**Praetor** — The central brain. A Python service (FastAPI + LangGraph)
that owns intent classification, conversation state, memory, policy,
and orchestration. The only component that holds long-lived
credentials. See [ADR-0006](adr/0006-praetor-runtime.md).

**Satellite** — A microphone/speaker endpoint that speaks the Wyoming
protocol. Wake word + ASR run locally on the satellite. See
[ADR-0008](adr/0008-voice-wyoming.md).

**Trunk-based development** — Short-lived branches off `main`,
squash-merged via PR. Release branches are created on demand for
hotfixes. See [ADR-0014](adr/0014-trunk-based-development.md).

**Worker** — Singular of Legion. A process that registers with Praetor
and handles a declared set of intents/tools.

**Wyoming** — Open protocol for voice assistant components, used by the
Home Assistant ecosystem. CAESAR's satellites speak Wyoming so we can
reuse the existing satellite hardware/firmware ecosystem.
