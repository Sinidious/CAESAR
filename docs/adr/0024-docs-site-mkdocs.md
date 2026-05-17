# 0024 — Documentation site on mkdocs-material + GitHub Pages

- Status: Accepted
- Date: 2026-05-17
- Deciders: @Sinidious
- Related issues / PRs: v1.0 docs slice (after [ADR-0023](0023-opentelemetry-tracing.md))

## Context

The repo already carries a fair bit of operator-facing documentation
in `docs/` — architecture, glossary, configuration reference,
operations runbook, security model, an index of 23 ADRs, the
roadmap. It's all readable on GitHub, but only as a flat tree.
Operators trying to learn CAESAR have to bounce between Markdown
files with no cross-search, no nav, no theme, and no way to tell at a
glance which doc is canonical and which is historical.

A real docs site fixes that without much work: mkdocs-material reads
the same Markdown the repo already ships, gives us search, syntax
highlighting, an admonition syntax, a nav side-bar, and a deploy step
that piggybacks on GitHub Pages.

## Decision

CAESAR will ship a public documentation site built with
**mkdocs-material**, served from **GitHub Pages** on the existing
repo at `https://sinidious.github.io/CAESAR/`. It is **single-version,
always latest**: the site reflects `main`, no `mike`, no version
selector. If we ever ship LTS branches we'll revisit; until then the
operational simplicity is worth more than the extra URL paths.

Sources live where they already do — in `docs/` and in repo-root
markdown files (`README.md`, `CONTRIBUTING.md`). We pull the
root-level files into the site via `docs/_root_*.md` thin wrappers
that include them, rather than duplicating content or symlinking
(symlinks don't survive Windows checkouts cleanly).

Build and deploy are driven by `.github/workflows/docs.yml`:

- On every push to `main`, build the site with `mkdocs build --strict`
  and publish via the official `actions/deploy-pages` flow (artifact
  upload + Pages deploy).
- On PRs that touch `docs/`, `mkdocs.yml`, or the workflow, run
  `mkdocs build --strict` as a check-only step. No deploy from PRs.

The site is fully owned by `docs.yml`; deploys are gated on the
existing branch-protection rules (PRs must pass the rest of CI first).

## Alternatives considered

- **Plain GitHub Markdown view** — Free, already works. Rejected:
  no search, no nav, no theme. The bar for operator on-boarding
  rises sharply once we have more than a handful of ADRs and we're
  past that point.
- **Versioned docs via `mike`** — Each release gets its own
  URL. Rejected: doubles deploy complexity for no current operator
  benefit (no one is pinned to an older release yet). Easy to bolt
  on later if we ever ship LTS branches.
- **Custom domain (e.g. `caesar.dev`)** — Better branding. Rejected
  for now because it requires DNS + CNAME maintenance outside the
  repo and brings no functional improvement. Adding a `CNAME` file
  to flip later is a one-line change.
- **Different generator (Docusaurus, MkDocs vanilla, mdBook,
  Sphinx)** — Docusaurus is React-heavy and overkill for a homelab
  project; mdBook would force us off Markdown's CommonMark dialect;
  Sphinx targets Python API docs primarily. mkdocs-material is the
  Python-native, batteries-included default for projects this shape.

## Consequences

### Positive

- Operators get a navigable site with full-text search, syntax
  highlighting, admonitions, and a consistent nav across docs.
- Doc rot becomes visible: `mkdocs build --strict` fails on broken
  internal links and missing nav entries. CI catches link rot.
- The site updates automatically on every `main` push; no manual
  release coordination.
- Zero infra: GitHub Pages is part of the existing repo.

### Negative

- Extra CI step on `docs/` PRs (build + deploy). Adds a minute to
  green-time on docs-only changes.
- One more required workflow to keep happy. We do *not* add it to
  the required-checks list — docs failures shouldn't block code
  PRs — so the cost stays low.
- Root markdown files (`README.md`, `CONTRIBUTING.md`) need thin
  include-wrappers under `docs/` so the nav can reach them.

### Neutral

- `mkdocs-material` and `mkdocs` move quickly; we pin minor versions
  in the `[docs]` extra and let Dependabot keep them current.
- The Pages URL is `sinidious.github.io/CAESAR/`. If we later move
  to a custom domain, the only changes are a `CNAME` file in `docs/`
  and a DNS record.

## References

- [mkdocs-material documentation](https://squidfunk.github.io/mkdocs-material/)
- [GitHub Pages with actions/deploy-pages](https://docs.github.com/en/pages)
- [ADR-0021](0021-dashboard-htmx.md) — the dashboard is a different
  surface (live operator console); the docs site is read-only
  reference.
