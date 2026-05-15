# 0015 — Ruff + mypy + pytest as the Python toolchain

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

A young Python project has to pick a linter, a formatter, a type
checker, and a test runner. The cost of getting this wrong is not
quality — it is friction. Tools that are slow, noisy, or
configuration-heavy get disabled within months by a tired
maintainer.

The Python ecosystem now has clear, fast defaults: `ruff` covers
linting and formatting in one binary, `mypy` (strict) remains the
gold standard for type checking, `pytest` is unchallenged as the
test runner.

## Decision

CAESAR uses:

- **`ruff` for linting and formatting.** Configured in
  `pyproject.toml`. Format on save (developer setup), `ruff check
  --fix` in pre-commit, and `ruff check` + `ruff format --check`
  in CI.
- **`mypy` in strict mode** for type checking. Strict by default
  (`strict = true`); `tests/` are relaxed slightly via per-module
  overrides so we don't fight fixtures.
- **`pytest`** as the runner, with `pytest-cov` for coverage.
  Filterwarnings are errors so we notice deprecations before they
  bite.
- **`pre-commit`** runs ruff, the standard hygiene hooks, gitleaks,
  and `conventional-pre-commit` on `commit-msg`. Installed via
  `just setup`.
- **`just`** as the task runner. Recipes live in the `Justfile`;
  `just check` is the canonical pre-push gate (lint + typecheck +
  test).
- **Python build backend: `hatchling`.** Smallest config that supports
  optional dependency groups and editable installs.

CI workflows:

- `lint.yml` runs ruff check + ruff format --check.
- `typecheck.yml` runs mypy.
- `test.yml` runs pytest on Python 3.11 and 3.12.

Branch protection on `main` requires those checks plus the CLA gate
([ADR-0003](0003-require-cla.md)).

## Alternatives considered

- **`black` + `isort` + `flake8` + `pylint`** — the previous
  generation. Slower, four binaries to keep configured.
- **`pyright`** — fast and excellent, but mypy's strict mode and
  ecosystem maturity win at this stage.
- **`unittest` + `nose2`** — workable, but pytest fixtures and
  parametrization are a productivity gap we don't need.
- **`tox`** — useful for matrix runs locally; we get the matrix from
  GitHub Actions instead and skip the extra tool.
- **`hatch` (the full project manager)** — promising; we use
  `hatchling` as the build backend but defer adopting hatch's
  scripts/environments to keep `just` as the single task entry
  point.

## Consequences

### Positive

- One binary (`ruff`) replaces three. Fast feedback loop.
- `just check` is one command and gates everything before push.
- Pre-commit catches the easy stuff before CI does.
- Strict mypy from day one prevents a slow drift into untyped code.

### Negative

- Some libraries lack type stubs; we will pay the `# type: ignore`
  tax occasionally. We log each as `[reason]` so they're easy to
  audit.
- New contributors have one more tool (`just`) to install. Documented
  in [CONTRIBUTING.md](../../CONTRIBUTING.md).

### Neutral

- Coverage threshold is intentionally not yet enforced. We will
  introduce one when we have a real codebase to threshold against;
  premature minimums distort effort.

## References

- [ruff](https://docs.astral.sh/ruff/)
- [mypy strict mode](https://mypy.readthedocs.io/en/stable/strict_settings.html)
- [pytest](https://docs.pytest.org/)
- [pre-commit](https://pre-commit.com/)
- [just](https://github.com/casey/just)
