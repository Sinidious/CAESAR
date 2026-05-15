# 0002 — License under PolyForm Noncommercial 1.0.0

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

CAESAR is built in the open as a homelab project. The author wants:

1. Personal, hobbyist, and research use to be unambiguously allowed
   and friction-free.
2. Commercial resale, hosted-as-a-service offerings, and
   white-labelling to be off-limits without a separate agreement.
3. To keep the option of dual-licensing (commercial terms) without
   re-licensing everything later.

A permissive license (MIT/Apache) gives away (2). AGPL keeps source
open but doesn't actually prevent commercial use, and its viral nature
is hostile to integrating with non-AGPL ecosystems. Source-available
licenses with a noncommercial clause hit the target directly.

## Decision

CAESAR is licensed under **PolyForm Noncommercial 1.0.0**.
A CLA (see [ADR-0003](0003-require-cla.md)) lets the maintainer offer
the same code under commercial terms when needed without re-licensing.

Implications:

- Personal, educational, and research use: allowed.
- Commercial use of any kind: requires a separate commercial license
  from the maintainer.
- Distribution must preserve the license notice (see
  [NOTICE](../../NOTICE)).
- Dependencies must be compatible with PolyForm-NC distribution.
  MIT / Apache-2.0 / BSD / ISC / MPL are fine. AGPL / SSPL / GPL in
  shipped code requires an ADR and almost certainly will not be
  approved.

## Alternatives considered

- **MIT / Apache-2.0** — clean, but allows commercial resale without
  any agreement.
- **AGPL-3.0** — protects against hosted-SaaS clones, but doesn't
  forbid commercial use and contaminates the dependency graph.
- **Business Source License (BSL)** — time-bounded commercial
  restriction is interesting, but the 4-year flip to Apache feels
  premature for a homelab project that may pivot.
- **Proprietary / source-closed** — defeats the homelab
  contribute-back goal.

## Consequences

### Positive

- Clear "homelab welcome, businesses talk to me" signal.
- Preserves the option of a commercial license without re-licensing.
- Forces the project to be intentional about transitive dependency
  licenses.

### Negative

- Not OSI-approved; some contributors will not contribute on principle.
- Some companies' open-source contribution policies block PolyForm-NC.
- We must occasionally explain to users that "open source" and "free
  for commercial use" are not the same.

### Neutral

- The CLA (ADR-0003) is what actually enables future commercial
  dual-licensing; the license alone is not enough.

## References

- [PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/)
- [LICENSE](../../LICENSE)
- [NOTICE](../../NOTICE)
