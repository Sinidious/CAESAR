# 0009 — NATS as the message bus

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

Praetor orchestrates a pool of worker processes (Legion). Workers may
be local (same host) or on a small homelab cluster, may be written
in something other than Python eventually, and need to:

- Register themselves and their capabilities.
- Receive task dispatches (request/reply semantics).
- Publish events that Praetor and the audit log subscribe to.

The bus also needs to be operationally cheap — a homelab project
cannot afford a Kafka cluster — and to survive a single-node
deployment.

## Decision

CAESAR uses **NATS** as the message bus. Specifically:

- **Core NATS** for low-latency request/reply (worker dispatch).
- **JetStream** for persistent streams where we need replay (audit log
  ingest, agent registry events).
- Subjects are namespaced:
  - `praetor.intent.*` — incoming, normalized intents.
  - `legion.<worker>.dispatch` / `.result` — task dispatch + reply.
  - `legion.registry.*` — registration / capability changes.
  - `bridge.ha.*` — HA Bridge events.
  - `audit.*` — append-only stream consumed by the audit log writer.
- Auth: NKEY-based, with each worker getting a scoped account.
- Default deployment: single binary, single node, JetStream enabled.

## Alternatives considered

- **Redis Pub/Sub + Streams** — viable, but Redis Streams are weaker
  than JetStream for replay and Redis adds little we'd actually use.
- **MQTT** — great for IoT, weak for request/reply and for ordered
  streams. Already in the stack via HA, but as a sensor/device
  protocol, not an inter-service bus.
- **Kafka / Redpanda** — overkill for a homelab. Operational burden
  exceeds the benefit.
- **gRPC point-to-point, no bus** — couples Praetor to every worker;
  worker discovery becomes our problem; no pub/sub for audit.
- **Just function calls inside Praetor** — works until workers want
  to be on a different host or a different language.

## Consequences

### Positive

- Single small binary, fast, well-documented operations.
- Subjects give us natural namespaces and per-area authorization.
- JetStream gives us the audit log pipeline for free.
- Multi-language workers later are a non-event.

### Negative

- One more daemon to run. Documented in
  [`docs/CONFIGURATION.md`](../CONFIGURATION.md).
- NATS clustering, if we ever need it, is non-trivial; out of scope
  for now.

### Neutral

- Local-only deployments can still use NATS without auth on
  `127.0.0.1`; we will not ship that as a default for any non-dev
  environment.

## References

- [NATS](https://nats.io/)
- [JetStream concepts](https://docs.nats.io/nats-concepts/jetstream)
- [NATS auth: NKEYs and JWTs](https://docs.nats.io/running-a-nats-service/configuration/securing_nats/auth_intro)
