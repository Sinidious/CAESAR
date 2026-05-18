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

- [x] ADR-0028: tool worker shape + Policy Engine generalisation
      + per-tool YAML grammar.
- [x] Policy generalisation: `evaluate(call)` accepts a uniform
      `ToolCall` shape; existing `ServiceCall` becomes a subtype;
      audit row carries the tool id.
- [x] Calculator worker: pure-Python, no network, no creds.
      Smallest possible end-to-end exercise of the new tool path.
- [x] Web-search worker: network call against a self-hosted
      SearXNG instance. Policy-gated by `allowed_tools` with an
      optional `domain_allowlist`.
- [x] Calendar-read worker: CalDAV against a homelab calendar.
- [x] Docs: ["Add your own tool"](ADD-YOUR-OWN-TOOL.md) page.

## v1.4 — Install in 10 minutes

**Question:** Can someone who isn't me deploy CAESAR on a fresh
NUC and have it running in 10 minutes?

v1.0–v1.3 made CAESAR functionally rich. v1.4 makes it *adoptable*:
a published Docker image, a reference Compose stack, and a
`caesar init` command that generates a self-contained working
config without making the operator hand-edit YAML. The bare-metal
pip path keeps working for operators who want it. ADR-0029 covers
the design.

- [x] ADR-0029: Docker + Compose distribution, `caesar init`
      config-only generator, ghcr.io image naming, multi-arch
      (amd64 + arm64), backward-compat with pip install.
- [x] `caesar init` command: writes `.env`, `policy.yaml`, a fresh
      dashboard token, a Praetor NKEY (for the multi-host upgrade
      path), and `./var/`. Idempotent; refuses to overwrite an
      existing config without `--force`.
- [x] `Dockerfile` (multi-stage; runtime image is Python 3.11-slim
      based; bakes the full default install including caldav and
      both LLM SDKs).
- [x] `docker-compose.yml` reference stack: CAESAR + nats-server
      with commented Ollama + SearXNG services for the
      privacy-conscious operator.
- [x] GitHub Actions: build + push multi-arch image to
      `ghcr.io/sinidious/caesar` on every release; the existing
      Sigstore provenance attestation (SR-011) covers the image.
- [x] Docs: ["Quickstart in 10 minutes"](QUICKSTART.md) wiring
      `caesar init` → `docker compose up` → "open the dashboard"
      end-to-end.

## v1.5 — Proactivity

**Question:** Can CAESAR do something without me asking?

Through v1.4 every brain run is operator-initiated. v1.5 lets CAESAR
*start* runs on its own — first via scheduled triggers, then by
reaching the operator's phone through ntfy.sh. The shape generalises
to HA-event and webhook triggers later (v1.6+), but v1.5 ships the
end-to-end proof with the simplest source. ADR-0030 covers the
design.

- [x] ADR-0030: proactive triggers (Trigger shape + ScheduleSource),
      `schedules.yaml` grammar, ntfy.sh notify sink as a Legion tool,
      `trigger.*` audit-log event types, proactive-run policy
      semantics, single-instance scheduler decision.
- [x] Scheduler subsystem: asyncio task per Praetor instance, reads
      `schedules.yaml`, fires triggers into the brain graph via
      `praetor.proactive.fire(...)` under `asyncio.timeout`. Uses
      croniter for cron-expr semantics; no APScheduler.
- [x] `notify` Legion worker backed by ntfy.sh: title + message +
      priority + tags input shape, audit-logged like every other
      tool, policy-gated under `allowed_tools`. Default settings
      target the public ntfy.sh server; self-hosted documented.
- [x] Proactive-run brain entry point: same graph as `/v1/chat`,
      with the safety preamble carrying `proactive=true` to bias
      toward summarise+notify over act-on-the-house.
- [x] `caesar init` writes a `schedules.yaml` with a
      disabled-by-default "morning_brief" example; adds `notify` to
      the default `allowed_tools` so the brain can use it once an
      operator enables a schedule.
- [x] End-to-end test: fake cron tick fires, brain runs against an
      LLM fake that emits a `notify` tool call, mock ntfy sink
      receives the POST, audit log carries `trigger.fired` +
      `notify.called` + `trigger.completed`.
- [x] Docs: ["Proactive CAESAR"](PROACTIVE-CAESAR.md) covering
      `schedules.yaml` grammar, ntfy.sh setup (free server vs
      self-host), policy entries, how to disable proactivity
      entirely.

## v1.6 — HA event triggers

**Question:** Can CAESAR react to my house without me asking?

v1.5 lets CAESAR fire on a schedule. v1.6 lets the same brain fire
in response to Home Assistant events — motion at 11pm, water leak
detected, garage door open too long. Same Trigger discriminated
union, same ProactiveRunner, same audit log; just a second source
variant (`HASource`) and a single shared HA WS subscription.
ADR-0031 covers the design.

The deliberate scope cut: the matcher stays coarse (event_type +
entity_id + to + time_window). Multi-condition logic lives in the
prompt, not in YAML — because the brain has full state access and
can reason in ways an automation rule can't.

- [x] ADR-0031: HASource trigger variant, simple-matcher grammar,
      single-WS-subscription posture, per-trigger cooldown, rename
      `schedules.yaml` → `triggers.yaml` with one-release deprecation,
      new `trigger.subscribed` / `trigger.suppressed` /
      `ha.subscription.*` audit event types.
- [x] HA WS event subscription extension: one shared subscription per
      Praetor instance, exponential-backoff reconnect with jitter,
      drop-on-disconnect replay policy (documented).
- [x] HASource Pydantic model + matcher: exact `event_type` match;
      optional `entity_id` + `to` constraints (state_changed only);
      optional minute-resolution `time_window` (cross-midnight ok)
      in the trigger's IANA timezone.
- [x] Per-trigger `cooldown_seconds`. Default None = fire every match.
      Cooldown suppressions coalesce into one `trigger.suppressed`
      audit row with a count.
- [x] HAEventDriver: subscribes once, demultiplexes incoming events
      to per-trigger matchers, hands matches to ProactiveRunner.
      Reuses the v1.5 brain entry unchanged.
- [x] `triggers.yaml` filename + `schedules.yaml` deprecation alias.
      `caesar init` writes `triggers.yaml` with a disabled-by-default
      HA-event example alongside the existing morning_brief.
- [x] End-to-end test: fake HA WS emits scripted state_changed events,
      matcher fires brain, cooldown suppresses follow-up events,
      audit-log carries `trigger.subscribed` + `trigger.fired` +
      `trigger.suppressed` + `trigger.completed`.
- [x] Docs: HA event triggers extension to
      [Proactive CAESAR](PROACTIVE-CAESAR.md) — matcher grammar
      with worked examples, the "matcher coarse, prompt smart"
      pattern, cooldown semantics, replay-on-reconnect gap, and
      the v1.5→v1.6 migration table.

## v1.7 — Webhook triggers

**Question:** Can external systems wake the brain by POSTing JSON?

v1.5 shipped scheduled triggers. v1.6 shipped HA events. v1.7 ships
the third Trigger source variant — HTTP webhooks — so any external
system (n8n, IFTTT, GitHub, calendar services, custom shell scripts)
can fire the brain. Same Trigger discriminated union, same
ProactiveRunner, same audit log; a new `WebhookSource` plus one
FastAPI route. ADR-0032 covers the design.

This also closes ADR-0031 §7's noted reliability gap: HA WS events
drop on disconnect, but webhooks have durable delivery because the
sender owns retry. Operators routing security-sensitive events
(water leak, smoke alarm) can prefer webhooks.

- [x] ADR-0032: `WebhookSource` trigger variant, per-trigger bearer
      token auth, fire-and-forget 202 response, body-in-prompt
      pattern, `webhook.*` audit event types, deferred-HMAC
      decision documented.
- [x] `WebhookSource` Pydantic model with `bearer_token: SecretStr`;
      flat-form YAML disambiguator extended (`bearer_token` ⇒
      webhook variant).
- [x] `POST /v1/hook/{trigger_id}` FastAPI route: `Authorization:
      Bearer` validated with `hmac.compare_digest`; 202 / 401 / 404
      / 413 / 429 contract; 64 KiB body limit at the edge.
- [x] `WebhookDispatcher`: per-trigger cooldown reused from v1.6;
      coalesced `trigger.suppressed` rows; fire-and-forget background
      task into `ProactiveRunner`; body formatted into the user
      message as "Event body: <JSON>".
- [x] Lifespan wiring alongside Scheduler + HAEventDriver. Route
      registered even with no webhook triggers armed (stable 404
      contract for debugging).
- [x] `caesar init` writes a disabled webhook example with a fresh
      `secrets.token_urlsafe(36)` bearer token alongside the existing
      morning_brief + late_office_motion examples.
- [x] End-to-end test: valid bearer → 202 + brain fires; wrong
      bearer → 401 + audit row; unknown trigger → 404; cooldown
      coalesces rapid repeats into `trigger.suppressed`.
- [x] Docs: webhook section extension to
      [Proactive CAESAR](PROACTIVE-CAESAR.md) — bearer token model,
      sample curl/n8n invocations, body-in-prompt pattern, network
      exposure note (loopback-by-default + how to expose), worked
      examples.

## v1.8 — Memory: remember what I told you (facts)

**Question:** If I told CAESAR a fact last week, does it remember
in today's chat without me reminding it?

Through v1.7 CAESAR has technical memory (audit log, semantic
recall) but no operator-visible memory. The brain doesn't fetch
context automatically and even when it does, recalled chat blobs
don't translate into "you told me your dog's name is Beans".

v1.8 ships a **personal-facts layer**: a background
`memory.extract` Legion worker reads recent `chat.completed` rows,
asks an LLM what facts the operator revealed, stores `{key, value,
confidence}` rows in a dedicated `personal_facts` table, and the
brain auto-injects relevant facts into the next chat's system
prompt. ADR-0033 covers the design.

v1.9 is the planned follow-up — episode summarisation, better
semantic recall integration, and decay/importance ranking that
cross-cuts both facts and episodes.

- [ ] ADR-0033: personal-facts schema, `memory.extract` worker
      shape, retrieval auto-inject, dashboard surface, natural-
      language template registry, privacy bounds.
- [ ] Alembic migration: `personal_facts` (id, key UNIQUE, value,
      confidence, first_seen_at, last_confirmed_at, source_audit_id)
      + `memory_extract_cursor` (single row). `FactsStore` class for
      CRUD with audit-row side-effects.
- [ ] `memory.extract` Legion worker: polls `chat.completed` rows
      since cursor; one LLM call per row via task-routed gateway
      (`task="memory_extract"`); writes facts with dedup-by-key
      semantics; audit rows for added / updated / confirmed.
- [ ] Retrieval + auto-inject: `/v1/chat` and the ProactiveRunner
      load current facts at the start of each run; system prompt
      grows a "What you've told me:" block rendered via a small
      template registry; capped at 30 facts / 2048 chars; toggle
      via `CAESAR_MEMORY__FACTS__ENABLED`.
- [ ] Dashboard `/dashboard/facts` page: list current facts with
      edit / delete / confidence display / source-audit link.
      Edits emit `memory.fact.user_edited` audit rows.
- [ ] End-to-end test: chat 1 tells CAESAR a fact; extractor runs;
      chat 2 (in a new session) verifies the brain has the fact via
      the system-prompt inject (assert via fake-gateway capture).
- [ ] Docs: new "Memory" page covering what gets extracted, when,
      how to inspect, how to correct, how to disable, and what
      *doesn't* land in facts.

## Out of scope (for now)

- Mobile native apps (the dashboard will be installable PWA first).
- Multi-tenant operation. CAESAR is for one household.
- Cloud-hosted CAESAR. Self-hosted only by design.
