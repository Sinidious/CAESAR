# 0003 — Require a CLA for outside contributions

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

CAESAR is licensed under PolyForm Noncommercial 1.0.0
([ADR-0002](0002-license-polyform-nc.md)). The maintainer wants to
preserve the option to offer the same code under commercial terms
in the future (dual-licensing) without contacting every prior
contributor for permission.

Under default copyright rules, each contributor retains copyright on
their contribution. Without explicit terms, the project cannot
relicense their work later — even to a more permissive license.

## Decision

Every contributor signs the [CAESAR CLA](https://github.com/Sinidious/CAESAR/blob/main/CLA.md) before their
first PR is merged. The CLA grants the maintainer a broad license to
use, sublicense, and dual-license contributed code, while leaving the
contributor as the copyright owner.

Enforcement: the
[`cla.yml`](https://github.com/Sinidious/CAESAR/blob/main/.github/workflows/cla.yml) workflow uses
**CLA Assistant Lite** (`contributor-assistant/github-action`) to:

- Comment on PRs from new contributors with the signing instruction.
- Record signatures in
  [`.github/cla-signatures.json`](https://github.com/Sinidious/CAESAR/blob/main/.github/cla-signatures.json).
- Block merges until the contributor signs by replying with the
  prescribed comment.

Bots (`dependabot[bot]`, `renovate[bot]`, `release-please[bot]`,
`github-actions[bot]`) are allow-listed.

## Alternatives considered

- **DCO (Developer Certificate of Origin)** — simpler, but only
  certifies that the contributor has the right to submit. It does
  not grant additional licensing rights, so it cannot support future
  relicensing.
- **No CLA, no DCO** — fastest to contribute, but locks the project
  into PolyForm-NC forever and complicates any future commercial
  arrangement.
- **Per-PR contributor agreement in the PR template** — same legal
  outcome as a CLA but without the audit trail or automated
  enforcement.

## Consequences

### Positive

- The maintainer can dual-license or relicense in the future without
  hunting down past contributors.
- Signatures are tracked in-repo, auditable, and survive a GitHub
  outage.
- A clear, repeated friction point keeps the legal posture explicit.

### Negative

- An extra step before a first contribution; some contributors will
  bounce off this.
- The CLA needs review by the maintainer before adoption; legal
  ambiguity sits on the project until then.

### Neutral

- The CLA does not change the license under which the project is
  distributed (still PolyForm-NC). It only governs the relationship
  between contributor and project.

## References

- [CLA.md](https://github.com/Sinidious/CAESAR/blob/main/CLA.md)
- [contributor-assistant/github-action](https://github.com/contributor-assistant/github-action)
- [Apache ICLA reference](https://www.apache.org/licenses/icla.pdf)
