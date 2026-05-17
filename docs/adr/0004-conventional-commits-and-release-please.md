# 0004 — Conventional Commits and release-please for changelogs and releases

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

CAESAR will ship versioned releases. The maintainer wants:

- A predictable changelog that doesn't depend on hand-writing notes.
- Automatic SemVer bumps from the kinds of changes that landed.
- Releases driven from `main` so anyone can read history and see what
  shipped and when.

Doing this by hand is error-prone for a single-maintainer project; the
release-please workflow is well-trodden in the Google open-source
ecosystem and handles the entire pipeline.

## Decision

CAESAR uses **Conventional Commits** for every commit on `main`, and
**release-please** to drive changelog generation and tagging.

- Commit message format: `<type>(<scope>): <imperative summary>`.
  Allowed types are enumerated in
  [`pr-lint.yml`](https://github.com/Sinidious/CAESAR/blob/main/.github/workflows/pr-lint.yml) and enforced
  on PR titles.
- `pre-commit` runs `conventional-pre-commit` on `commit-msg` so local
  commits also have to conform.
- The
  [`release-please.yml`](https://github.com/Sinidious/CAESAR/blob/main/.github/workflows/release-please.yml)
  workflow runs on every push to `main`. It opens a release PR that
  bumps `pyproject.toml`, updates `CHANGELOG.md`, and tags
  `vX.Y.Z` on merge.
- `release-please-config.json` maps types to changelog sections; `feat`
  and `fix` are user-visible, `chore`/`ci`/`test` are hidden by default.

PR titles are linted by
[`amannn/action-semantic-pull-request`](https://github.com/amannn/action-semantic-pull-request)
in `pr-lint.yml`. Squash-merge uses the PR title as the commit subject,
so this is the binding gate.

## Alternatives considered

- **Hand-rolled `CHANGELOG.md`** — works at scale-of-one but rots fast
  and tempts skipping during busy weeks.
- **`semantic-release`** — JS-first, fine but adds a Node toolchain
  just for releases.
- **`git-cliff`** — beautiful changelogs, no release PR / tagging
  workflow; we'd still need to wire that ourselves.
- **`commitizen`** — strong contender for the bump/tag side; rejected
  to avoid running it locally on a single-maintainer project — CI
  is the bottleneck, not the developer.

## Consequences

### Positive

- Every release has a clean, sectioned changelog with no manual work.
- PR titles are the durable record (squash-merge), and they're
  validated automatically.
- New contributors get a quick rejection from CI rather than a vague
  review comment.

### Negative

- "I just want to push a quick fix" requires remembering the
  `fix(scope): …` shape. Pre-commit hooks make this less painful.
- The release PR adds a step between "merge to main" and "tag" —
  releases are not instant.

### Neutral

- We can revisit the type list in `pr-lint.yml` and the section map in
  `release-please-config.json` without breaking history.

## References

- [Conventional Commits](https://www.conventionalcommits.org)
- [release-please](https://github.com/googleapis/release-please)
- [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
