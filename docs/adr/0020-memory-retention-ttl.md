# 0020 — Episodic memory retention via time-based TTL

- Status: Accepted
- Date: 2026-05-17
- Deciders: @sinidious

## Context

[ADR-0010](0010-memory-hybrid.md) (hybrid memory) explicitly punted on
retention: *"Memory retention policy (TTL, GDPR-style erase-on-request)
is its own decision and will get its own ADR when we implement it."*
[ADR-0012](0012-audit-log.md) commits CAESAR to writing one audit row
for every brain decision; left unchecked, the file grows without bound.

The forcing function for this ADR is v0.4 (*Memory that sticks*) — we
now have enough write traffic (`chat.completed`, `service.called`,
`policy.denied`, sweep events) that a homelab instance left running
for a year will accumulate millions of rows. The
`memory_recall` worker reads from the same table; query latency starts
to hurt long before disk is the bottleneck.

We need a cap that the operator understands without reading code.

## Decision

CAESAR retains audit-log rows for **a time-based TTL**, default
**90 days**. Specifically:

- One setting: `CAESAR_MEMORY__RETENTION_DAYS` (default 90).
- Praetor runs a background sweep at lifespan startup, then every
  `CAESAR_MEMORY__SWEEP_INTERVAL_SECONDS` (default 3600). Each sweep
  deletes rows where `ts < now() - retention_days`.
- Each sweep writes one audit row labelled `memory.retention_sweep`
  with `{"deleted": N, "cutoff": "<iso ts>"}`. The sweep row itself is
  subject to the same TTL — sweeps eventually delete their own
  history. Acceptable.
- One CLI command for explicit one-shot maintenance:
  `caesar memory sweep --dry-run` (count, no delete) and
  `caesar memory sweep --apply` (delete). Useful when the operator
  wants to free space immediately or run a manual cleanup as part of
  a backup ritual.
- No per-event-type retention overrides in this ADR; if we need them
  later they'll be a follow-up.

The sweep is fail-soft: if a sweep raises, log a warning and try again
at the next interval. The brain stays up.

## Alternatives considered

- **Count-based cap per event_type** — keep the most recent N rows of
  each kind. Bounded storage but old-but-still-meaningful context for
  low-frequency events (`policy.denied`) sticks around longer than the
  operator probably expects. TTL is easier to explain.
- **TTL + count cap (both)** — richer, more knobs to misunderstand.
  Add it if a single dimension proves insufficient.
- **No retention; manual VACUUM only** — works for a single user with
  attention to disk usage. Not a default we want to ship.
- **Archive-to-cold-storage before delete** — pull old rows to a
  compressed flat file the operator can browse offline. Worth doing
  for compliance scenarios, not for v0.4. Reconsider when there's a
  real "show me what we said three years ago" requirement.
- **GDPR-style erase-on-request** — a different feature; out of scope
  for retention. A future ADR will cover targeted deletion by user /
  conversation id.

## Consequences

### Positive

- One number an operator can reason about (`90 days`). Bumps and
  trims are env-var changes; no migration.
- Storage growth is bounded by a constant times the daily event rate.
- The dashboard (v0.5) can show "you have X days of history" without
  guessing.
- The sweep produces its own audit trail so anomalies are visible.

### Negative

- Permanent record of a one-off event from 6 months ago is gone.
  Operators who need that have to bump `RETENTION_DAYS` (and accept
  the storage cost) or wait for the archive ADR.
- A sweep loop is one more thing that can fail silently. Mitigated by
  the audit row and the warning log on errors.

### Neutral

- Vector embeddings (semantic memory; v0.4 PR B) get their own
  retention story tied to the source row's deletion. If the audit row
  goes, its embedding goes with it. That's a constraint the semantic
  recall worker will need to respect.
- Manual `VACUUM` after large sweeps is the operator's call; SQLite
  reclaims space only on `VACUUM`. We don't run it automatically
  because it locks the database.

## References

- [ADR-0010 — Hybrid memory](0010-memory-hybrid.md)
- [ADR-0012 — Audit every brain decision](0012-audit-log.md)
- [ADR-0019 — SQLite persistence](0019-sqlite-persistence.md)
- [SQLite VACUUM](https://sqlite.org/lang_vacuum.html)
