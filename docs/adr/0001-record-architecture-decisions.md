# 0001 — Record architecture decisions

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

CAESAR is a personal-but-public homelab project. Choices made now —
how the brain runs, what the message bus is, how voice flows in — will
be cheap to question in a year and expensive to change. With one
maintainer today and an unknown number tomorrow, the rationale behind
those choices needs to outlive the conversation that produced it.

## Decision

CAESAR records non-trivial technical decisions as Architecture Decision
Records (ADRs) in [MADR](https://adr.github.io/madr/) format. ADRs live
in `docs/adr/`, are numbered sequentially, and are immutable once
accepted — a reversal is a new ADR that supersedes the old one.

New application code in a new area (a new module, new external
dependency, or new architectural pattern) requires an accepted ADR
first. Small fixes, refactors, tests, and doc changes do not.

## Alternatives considered

- **No ADRs, decisions live in PR descriptions.** Cheaper today,
  unsearchable tomorrow. PR descriptions are not indexed in the docs
  site and rot when repos are mirrored.
- **A single `DECISIONS.md` file.** Conflicts on merges; no natural
  per-decision discussion thread.
- **Notion / Obsidian / a wiki.** Decouples decisions from the code
  that implements them. The repo is the source of truth.

## Consequences

### Positive

- Reviewers can ask "where's the ADR?" and that is a complete review
  comment.
- Contributors (human or AI) reading [CLAUDE.md](https://github.com/Sinidious/CAESAR/blob/main/CLAUDE.md) have
  a hard gate against scope creep.
- The docs site automatically renders the full decision history.

### Negative

- Some friction for small architectural ideas — the proposer has to
  write a short doc before code lands.
- Requires discipline to keep ADRs short (MADR helps).

### Neutral

- We will write some ADRs that get rejected. We merge those too, with
  status `Rejected`, so the reasoning isn't lost.

## References

- [MADR template](https://adr.github.io/madr/)
- [Michael Nygard's original ADR post](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
