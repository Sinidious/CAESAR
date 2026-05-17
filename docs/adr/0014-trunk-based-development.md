# 0014 — Trunk-based development with release branches as needed

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

CAESAR is maintained by one person today, with the expectation of
occasional outside contributors. Choosing a branching model now sets
the contract for PR titles, release tagging, and CI scope.

GitFlow's `develop`/`release`/`hotfix` topology assumes parallel
release trains and scheduled deploys; CAESAR has neither. GitHub Flow
("everything off `main`, deploy on merge") is the simplest workable
model for a single-trunk project. Release branches, when needed, can
be carved off `main` at the tag.

## Decision

CAESAR uses **trunk-based development**:

- `main` is the trunk and the only long-lived branch.
- Feature work happens on short-lived branches off `main` named with
  a category prefix:
  - `feature/<slug>`
  - `fix/<slug>`
  - `docs/<slug>`
  - `chore/<slug>`, `refactor/<slug>`, `test/<slug>`, `ci/<slug>`,
    `build/<slug>`, `perf/<slug>`
  - `claude/<slug>` for AI-assisted sessions (e.g. the
    `claude/setup-caesar-framework-9KfgU` branch).
  - `release/v<X.Y>` for hotfix branches when we need them (we do
    not preemptively create these).
- PRs are **squash-merged**. The PR title — validated by
  [`pr-lint.yml`](https://github.com/Sinidious/CAESAR/blob/main/.github/workflows/pr-lint.yml) — becomes the
  commit subject on `main`.
- Branch protection on `main`: required status checks (lint,
  typecheck, test 3.11 + 3.12, CLA Assistant Lite), required CODEOWNER
  review, linear history, no force-push.
- Releases are cut by `release-please` from Conventional Commits on
  `main` ([ADR-0004](0004-conventional-commits-and-release-please.md)).
- **Release branches are created on demand only** — when a bug needs
  patching on a tagged release that isn't `main`. We cherry-pick fixes
  from `main` to the release branch and tag a patch release from
  there.

## Alternatives considered

- **GitFlow** — heavier process than a homelab project needs; two
  long-lived branches double the CI surface.
- **GitHub Flow (no branch-name convention)** — close to what we
  picked; rejected only because the `pr-lint.yml` branch-name check
  gives us free guard rails for very little cost.
- **Trunk-only commits (no PRs)** — fine for solo work, hostile to
  review and to CI gating. Bad fit once contributors arrive.

## Consequences

### Positive

- One branch to think about. CI scope is small.
- Squash-merge + Conventional-Commit PR titles give release-please
  exactly the input it needs.
- New contributors only need to learn `feature/<slug>` and "open a PR".

### Negative

- A serious incident on a tagged release requires creating a release
  branch ad-hoc. Documented in this ADR so future-us isn't surprised.
- No `develop` branch means we cannot stage a half-finished release.
  Feature flags or branch-per-feature stay our tool for that.

### Neutral

- The AI-session branch prefix (`claude/`) is explicit in the lint
  rules so cloud-driven sessions don't trip CI on their own pushes.

## References

- [Trunk-Based Development](https://trunkbaseddevelopment.com/)
- [Conventional Commits](https://www.conventionalcommits.org)
- [`pr-lint.yml`](https://github.com/Sinidious/CAESAR/blob/main/.github/workflows/pr-lint.yml)
