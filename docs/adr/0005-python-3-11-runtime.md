# 0005 — Python 3.11 as the primary runtime

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

CAESAR is a long-running service with concurrency (HTTP server, NATS
client, WebSocket to Home Assistant), an LLM ecosystem written
overwhelmingly in Python, and a maintainer who wants to ship — not
fight a runtime. Picking the wrong Python version costs us either
performance, library availability, or both.

Python 3.11 brought significant CPython speedups, real `Self` and
`Required[]` typing primitives, and `ExceptionGroup` / `except*`. 3.12
extended that further but is not yet on every long-term-support distro.

## Decision

CAESAR targets **Python 3.11 as the primary runtime** and **tests
against 3.11 and 3.12 in CI**. The repo enforces this via
`requires-python = ">=3.11"` in `pyproject.toml` and the matrix in
`.github/workflows/test.yml`.

When 3.13 lands on Debian stable and the LangGraph/LangChain ecosystem
has caught up, we add it to the matrix; we drop 3.11 only when an
upstream we depend on does.

## Alternatives considered

- **Python 3.10** — still supported, but we lose the 3.11 typing
  niceties (`Self`, exception groups) and the CPython speedups for no
  benefit on a fresh project.
- **Python 3.12 only** — aggressive; the homelab ecosystem (HA, voice
  satellites, third-party libraries) still skews to 3.11.
- **Pyright/mypy on a polyglot runtime, e.g. PyPy** — overcomplicates
  the deployment story.

## Consequences

### Positive

- Free CPython performance wins (~10–25% over 3.10 on real workloads).
- `Self`, `ExceptionGroup`, `tomllib`, and `typing.TypeAlias` available
  without `typing_extensions`.
- A two-version test matrix catches accidental 3.12-only or
  3.11-only constructs.

### Negative

- Distros stuck on 3.10 need `pyenv` or `uv`. Documented in
  [`docs/CONFIGURATION.md`](../CONFIGURATION.md).
- Some libraries (rare) lag 3.12 support; we treat that as a constraint
  on adoption, not a CI failure.

### Neutral

- `.python-version` is set to `3.11` so `pyenv` picks the right one.
  CI overrides with its matrix.

## References

- [PEP 657](https://peps.python.org/pep-0657/) — fine-grained tracebacks (3.11)
- [PEP 654](https://peps.python.org/pep-0654/) — exception groups (3.11)
- [What's new in Python 3.11](https://docs.python.org/3/whatsnew/3.11.html)
