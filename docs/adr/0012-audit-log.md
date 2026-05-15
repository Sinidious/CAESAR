# 0012 — Audit every brain decision

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

CAESAR will make decisions that affect a real home, sometimes
proactively, sometimes wrongly. When something happens that wasn't
asked for — or didn't happen that should have — the maintainer needs
to be able to answer **why** without re-running the day. "It's an LLM,
shrug" is not a debugging strategy.

There is also a softer requirement: the maintainer wants to trust
this system enough to leave it running. That trust is bought with
visibility.

## Decision

Every decision Praetor makes is written to an **audit log** as an
append-only, structured record. Specifically:

- **One row per decision**, capturing: timestamp, decision id, inputs
  (intent, conversation id, satellite id), the policy verdict, the
  worker(s) consulted, the LLM call(s) made (provider, model, token
  counts, *not* full prompts unless DEBUG-level retention is on),
  the action emitted (if any), and the outcome from the HA Bridge.
- **JSONB-style "context" column** for free-form structured detail
  per decision type.
- **Same SQLite store** as episodic memory ([ADR-0010](0010-memory-hybrid.md))
  and LangGraph checkpointer state, so a backup of the SQLite file is
  a backup of everything that explains a day.
- **Streamed to NATS** on `audit.*` so the dashboard and external
  observers can subscribe.
- **Replayable**: given a decision id, we can re-fetch inputs and
  re-run the LangGraph node deterministically. LLM calls during
  replay use cached responses, not fresh calls.

## Alternatives considered

- **Application logs as the audit trail** — unstructured, easy to
  break, easy to drop.
- **A separate audit database (Postgres)** — operationally more, and
  splits the backup story for no clear gain.
- **Audit only "actions", not "decisions"** — misses the most common
  failure mode: deciding *not* to act when we should have.
- **Best-effort logging via a logger handler** — too easy to swallow
  failures. Audit writes must be a first-class step in the graph.

## Consequences

### Positive

- "Why did it do that?" has an answer.
- The dashboard has a real timeline to render.
- Replay enables regression tests for past incidents.

### Negative

- Every LangGraph node has to think about its audit shape. We trade
  developer convenience for operability.
- Storage will grow. Retention policy needs an ADR before we ship
  v1.0.

### Neutral

- Per-decision opt-in to *prompt-level* retention (full text of the
  LLM call) is a configurable knob, default off. Some operators will
  want it; many won't.

## References

- [LangGraph persistence](https://langchain-ai.github.io/langgraph/concepts/persistence/)
- [Event sourcing primer](https://martinfowler.com/eaaDev/EventSourcing.html)
