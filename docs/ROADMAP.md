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

**Question:** Can I say "turn on the kitchen light" out loud and have
it happen?

- HA Bridge (REST + WS, single token).
- Voice Satellite via Wyoming.
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

- All v0.x gates passing.
- Stability + observability: metrics, traces, alerting.
- Documented backup/restore.
- Security review of the policy engine.
- Public docs site.

## Out of scope (for now)

- Mobile native apps (the dashboard will be installable PWA first).
- Multi-tenant operation. CAESAR is for one household.
- Cloud-hosted CAESAR. Self-hosted only by design.
