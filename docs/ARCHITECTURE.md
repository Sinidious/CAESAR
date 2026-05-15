# Architecture

This document is the bird's-eye view. For the *why* behind any specific
choice, follow the link to the relevant ADR.

> Pre-alpha. The diagram below is the target shape, not what currently
> runs. As subsystems land, this page will get progressively less
> aspirational.

## Components

```
                       ┌───────────────────────┐
   Voice Satellite ──► │                       │ ◄── Dashboard (web)
   (Wyoming)           │                       │
                       │       Praetor         │
   HA Bridge ◄────────►│  (FastAPI+LangGraph)  │ ◄── External APIs
   (REST + WS)         │                       │     (LLM providers)
                       │                       │
                       └──────────┬────────────┘
                                  │
                          Message Bus (NATS)
                                  │
                  ┌───────────────┼────────────────┐
                  ▼               ▼                ▼
              Legion W1       Legion W2        Legion W3
             (e.g. RAG)      (e.g. tools)    (e.g. memory)
```

## Praetor — the central brain

- Python service. FastAPI for HTTP/WS, LangGraph for the state machine.
  See [ADR-0006](adr/0006-praetor-runtime.md).
- Owns intent classification, conversation state, policy decisions,
  memory reads/writes, and orchestration of Legion workers.
- Emits an audit-log entry for every decision; see
  [ADR-0012](adr/0012-audit-log.md). Decisions must be replayable.

## Legion — worker agent pool

- Each worker registers with Praetor over the message bus and declares:
  - Capabilities (intents/tools it can satisfy).
  - Cost/latency hints.
  - Required policy scopes.
- Workers are isolated processes (later: containers). They never talk
  to Home Assistant or external APIs directly — Praetor and the
  Policy Engine mediate side effects.

## HA Bridge

- Single Python module that owns the connection to Home Assistant
  (REST + WebSocket). See [ADR-0007](adr/0007-home-assistant-bridge.md).
- Translates HA entities/services into a Praetor-friendly interface.
- All real-world actions flow through the Policy Engine first.

## Voice Satellite

- Mic/speaker endpoint speaking the Wyoming protocol. See
  [ADR-0008](adr/0008-voice-wyoming.md).
- Wake word + ASR run on the satellite; TTS may run on the satellite or
  Praetor depending on hardware.

## Memory

- Hybrid: SQLite for episodic / structured memory, a vector store for
  semantic recall. See [ADR-0010](adr/0010-memory-hybrid.md).

## LLM Gateway

- Provider-agnostic abstraction over Anthropic, OpenAI, Ollama, vLLM,
  Groq, and friends. See [ADR-0011](adr/0011-llm-gateway.md).
- Per-agent personalities and task priorities are configured here, not
  in individual workers.

## Message Bus

- NATS (per [ADR-0009](adr/0009-message-bus-nats.md)).
- Subject-based routing: `praetor.intent.*`, `legion.<worker>.*`,
  `bridge.ha.*`, `audit.*`.

## Audit Log

- Every brain decision is written, append-only, with the inputs that
  produced it. Operators can replay a day to investigate why CAESAR
  did what it did. See [ADR-0012](adr/0012-audit-log.md).

## Policy Engine

- Gatekeeper between Praetor/Legion and the real world. See
  [ADR-0013](adr/0013-policy-engine.md).
- Enforces things like "do not unlock doors after midnight unless the
  primary user is home" — declarative rules, not hard-coded checks.

## Trust boundaries

See [SECURITY-MODEL.md](SECURITY-MODEL.md). Short version: voice
satellites and the dashboard are untrusted user-input surfaces;
Praetor is the only component that holds long-lived credentials;
workers run with the minimum scope they declared at registration.
