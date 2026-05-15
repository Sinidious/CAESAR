# 0010 — Hybrid memory: SQLite for episodic, vector store for semantic

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

CAESAR needs to remember things across turns and across days. Two
shapes of memory matter:

1. **Episodic / structured** — "what happened, when, by whom, in
   response to what?" This is the audit log's domain and also where
   conversation history lives. Queryable by time, by user, by area.
2. **Semantic / unstructured** — "I told it I prefer the bedroom
   slightly cooler than the rest of the house." Retrieved by
   similarity, not by key.

A single store optimized for one of these is bad at the other.

## Decision

CAESAR uses a **hybrid memory** with two backends:

- **Episodic memory: SQLite.** Same database that LangGraph
  ([ADR-0006](0006-praetor-runtime.md)) checkpoints into, plus the
  audit log ([ADR-0012](0012-audit-log.md)). Single file, easy
  backup, fast for the cardinality of a household.
- **Semantic memory: a vector store** behind a small abstraction.
  Default backend is `sqlite-vss` (zero ops, single file). Operators
  can swap in Qdrant or Chroma via `CAESAR_VECTOR_BACKEND` when they
  outgrow `sqlite-vss`.

Writes to memory are mediated by a dedicated Legion worker so the
storage layer is swappable, and so writes can be audit-logged like any
other side effect.

## Alternatives considered

- **A single vector DB for everything** — bad at time-range and
  structured queries; you end up bolting SQL on top.
- **A single RDBMS with pgvector** — solid choice, but Postgres in a
  homelab is more daemon than `sqlite-vss` deserves on day one.
- **Knowledge graph (e.g. Neo4j)** — overkill until we have entities
  worth a graph.
- **No persistent memory; everything in conversation context** —
  doesn't survive restarts, doesn't survive context window limits,
  doesn't audit.

## Consequences

### Positive

- One SQLite file backs LangGraph state, audit log, and episodic
  memory — single backup story.
- `sqlite-vss` default means new operators have nothing extra to
  install.
- Vector backend is replaceable without touching the rest of CAESAR.

### Negative

- Multiple stores mean multiple consistency stories. Praetor must
  treat them as eventually consistent.
- `sqlite-vss` has limits; operators with very large semantic memory
  will eventually migrate.

### Neutral

- Memory retention policy (TTL, GDPR-style erase-on-request) is its
  own decision and will get its own ADR when we implement it.

## References

- [sqlite-vss](https://github.com/asg017/sqlite-vss)
- [Qdrant](https://qdrant.tech/)
- [Chroma](https://www.trychroma.com/)
