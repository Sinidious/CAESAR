# 0025 — Lightweight security review as a living document

- Status: Accepted
- Date: 2026-05-17
- Deciders: @Sinidious
- Related issues / PRs: v1.0 closing slice; closes the v1.0 gate.

## Context

CAESAR is shipping v1.0 as a daily-driver-ready homelab brain. It
controls real devices through Home Assistant, holds long-lived
provider keys, exposes a dashboard, and runs whatever an LLM tells
it to (mediated by the Policy Engine and Audit Log). Before
declaring "daily-driver ready" we should be honest about the trust
boundaries and where the current implementation is weakest.

[ADR-0013](0013-policy-engine.md) committed CAESAR to a mediating
Policy Engine; [docs/SECURITY-MODEL.md](../SECURITY-MODEL.md)
describes the intended trust model. Neither names the *gaps* — the
specific places where reality lags the model. v1.0 is also the
right time to set expectations for *how* future security reviews
happen, because as the project grows the cost of an unstructured
audit grows with it.

## Decision

CAESAR will adopt a **lightweight, living security review**:

- A single, version-controlled file at
  [`docs/SECURITY-REVIEW.md`](../SECURITY-REVIEW.md) captures the
  current trust boundaries, what the implementation does well, and
  a numbered list of identified gaps with severity (Low / Medium /
  High / Critical).
- Each gap has a stable identifier (`SR-001`, `SR-002`, …). When a
  gap is closed, the row stays — the **Closed by** column points at
  the PR/commit that fixed it. We never renumber.
- Gaps that warrant fixes get GitHub issues, labelled
  `security`. The review file links each row to its issue. Severity
  + a quick rationale lives in the file; detailed reproduction lives
  in the issue.
- The review is **not** a formal STRIDE pass. It is a homelab
  defensibility audit: trust boundaries + a hardening checklist.
  Anything that requires nation-state-grade modelling is out of
  scope by design.
- Cadence: re-read the document at every minor release (v1.x). Any
  new ADR that introduces a trust boundary (a new external
  integration, a new auth surface) must add or revise a row.
- This ADR records the process and the *fact* that the first review
  has happened. It does not duplicate the review's content.

The review is doc-only at v1.0; identified fixes ship as separate
PRs so each gap closes cleanly with its own change.

## Alternatives considered

- **Full STRIDE threat model** — Per-subsystem
  Spoofing/Tampering/Repudiation/Info-Disclosure/DoS/Elevation pass
  with attack trees. Rejected: appropriate for a commercial
  product; overkill for a single-operator homelab brain, and the
  upkeep cost is high.
- **External pen-test scope doc only** — Write `SECURITY-SCOPE.md`
  describing what an external pen-tester would target, but defer
  the actual review until one is engaged. Rejected: cheap but
  punts on the most useful part — knowing where the implementation
  currently leaks.
- **Bundle the review with hardening PRs in one giant PR** —
  Rejected: makes the review unreviewable. Identifying gaps and
  fixing them are different operations and should go through CI
  separately.
- **No security review** — The current
  [`SECURITY-MODEL.md`](../SECURITY-MODEL.md) and
  [`SECURITY.md`](https://github.com/Sinidious/CAESAR/blob/main/SECURITY.md)
  cover the *intended* model and the disclosure process. Rejected:
  intent isn't audit — operators deploying v1.0 deserve to know
  what *is*, not just what we *meant*.

## Consequences

### Positive

- Operators have a single, current view of "what should I worry
  about with v1.0?".
- Security work becomes track-able: each `SR-NNN` row is a
  ticket-shaped artefact that can ship at its own cadence.
- New ADRs that introduce trust boundaries are gently forced
  through the review (by convention; no automation enforces it).
- Future contributors have a model for *how* to flag a concern
  without raising a CVE-style alarm.

### Negative

- The file will drift. Mitigated by the per-minor-release re-read
  cadence and by tying new trust boundaries to ADR additions.
- Severity ratings are subjective. The review names a rubric in
  its header to keep that bounded.

### Neutral

- Coordinated-disclosure stays in
  [`SECURITY.md`](https://github.com/Sinidious/CAESAR/blob/main/SECURITY.md)
  and uses GitHub's private vulnerability reporting. The review
  file is for *known* gaps; the disclosure path is for *novel* ones.

## References

- [docs/SECURITY-REVIEW.md](../SECURITY-REVIEW.md) — the living
  document this ADR creates.
- [docs/SECURITY-MODEL.md](../SECURITY-MODEL.md) — the intended
  trust model.
- [SECURITY.md](https://github.com/Sinidious/CAESAR/blob/main/SECURITY.md)
  — coordinated-disclosure policy.
- [ADR-0012](0012-audit-log.md), [ADR-0013](0013-policy-engine.md),
  [ADR-0021](0021-dashboard-htmx.md) — the three subsystems the
  first review leans on most.
