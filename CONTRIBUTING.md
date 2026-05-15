# Contributing to CAESAR

Thanks for your interest in CAESAR. This document covers the workflow,
tooling, and ground rules. Read it before opening your first PR.

> CAESAR is licensed under [PolyForm Noncommercial 1.0.0](LICENSE).
> Contributions are accepted under the terms of the [CLA](CLA.md).
> The CLA Assistant bot will prompt you to sign on your first PR.

## Quick links

- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Architecture Decision Records](docs/adr/)

## Development environment

CAESAR uses Python 3.12 and [`uv`](https://github.com/astral-sh/uv) for
package management.

```sh
# install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# clone & set up
git clone https://github.com/sinidious/caesar.git
cd caesar
just setup   # installs deps + pre-commit hooks
```

Common commands (see `Justfile`):

| Command           | What it does                              |
| ----------------- | ----------------------------------------- |
| `just setup`      | Install deps + register pre-commit hooks  |
| `just lint`       | `ruff check` + `ruff format --check`      |
| `just fmt`        | Auto-format with `ruff format`            |
| `just typecheck`  | Run `mypy`                                |
| `just test`       | Run `pytest`                              |
| `just check`      | All of the above                          |
| `just adr-new "Title"` | Scaffold a new ADR                   |
| `just docs-serve` | Serve the mkdocs site locally             |
| `just docs-build` | Build the mkdocs site                     |

## ADR-first workflow

Architectural changes — new modules, new external dependencies, new
patterns — require an [Architecture Decision Record](docs/adr/) before
the code lands.

1. `just adr-new "Use X for Y"` creates the next-numbered ADR.
2. Fill out **Context**, **Decision**, **Consequences**, and
   **Alternatives considered**.
3. Open a PR with **just the ADR** first when the decision is contentious.
4. Once the ADR is `Accepted`, implementation PRs may follow.

For bug fixes, refactors, doc edits, or test additions, no ADR is required.

## Branching & commits

- Branches: `feature/<short-desc>`, `fix/<short-desc>`, `docs/<short-desc>`,
  `chore/<short-desc>`, etc.
- Rebase onto `main` before opening a PR; keep history clean.
- Commit messages use [Conventional Commits](https://www.conventionalcommits.org):

  ```
  feat(praetor): add /healthz endpoint
  fix(bridge/ha): handle WS reconnect on token refresh
  docs(adr): accept ADR-0009 (NATS message bus)
  ```

- One logical change per commit; squash on merge if reviewers ask.

Release tagging and `CHANGELOG.md` are automated by `release-please`.
Don't hand-edit either.

## Pull request checklist

Before requesting review:

- [ ] CI is green (`lint`, `typecheck`, `test (3.11)`, `test (3.12)`)
- [ ] You've signed the [CLA](CLA.md) (the bot will check)
- [ ] If architectural: an `Accepted` ADR exists
- [ ] New behavior is covered by tests
- [ ] Docs updated where user-visible (README, ARCHITECTURE, ROADMAP, etc.)
- [ ] No new AGPL/SSPL/GPL transitive dependencies (ADR required if so)
- [ ] No "Generated with Claude" or AI-attribution footers anywhere

## Code style

- Python: `ruff` (lint + format), `mypy --strict` where possible.
- Type-annotate everything (`from __future__ import annotations`).
- Prefer composition over inheritance; explicit over implicit.
- Write the test first when fixing a bug.
- Keep functions small; if a function needs a comment to explain *what*
  it does, split it.

## Reporting bugs & requesting features

- Bugs: use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml).
- Features: use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.yml).
- Security: **do not file public issues**. Use [GitHub private security
  advisories](https://github.com/sinidious/caesar/security/advisories/new).

## Code of Conduct

By participating, you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).
Conduct concerns go through GitHub private security advisories as well.

## License of your contributions

By submitting a contribution, you agree it is licensed under the
[PolyForm Noncommercial License 1.0.0](LICENSE) and that you grant
Phillip Winnings the rights described in the [CLA](CLA.md), including
the right to relicense your contribution under other licenses in the
future.
