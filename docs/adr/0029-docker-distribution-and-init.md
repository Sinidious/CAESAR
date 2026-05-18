# 0029 — Docker distribution + `caesar init` for 10-minute install

- Status: Accepted
- Date: 2026-05-17
- Deciders: @Sinidious
- Related issues / PRs: v1.4 milestone; extends
  [ADR-0011](0011-llm-gateway.md) (LLM SDK install footprint),
  [ADR-0017](0017-configuration.md) (env layering),
  [ADR-0022](0022-backup-restore.md) (where state lives),
  [ADR-0027](0027-nats-auth-multihost-legion.md) (NKEY bootstrap).

## Context

CAESAR v1.0 → v1.3 shipped real capability — a daily-driver brain
that controls HA, runs against any of three LLM providers,
distributes workers across hosts, and calls calculator / search /
calendar tools. The functional gate is closed. The *adoption* gate
isn't.

A new operator today has to:

1. Read the README, then `CONTRIBUTING.md`, then `OPERATIONS.md`.
2. `pip install caesar` (assumes Python 3.11+ already present).
3. Hand-write `.env` with the right `CAESAR_LLM__PROVIDER`,
   `CAESAR_DASHBOARD__TOKEN` (with appropriate entropy), and
   `CAESAR_HA__URL` / `TOKEN`.
4. Hand-write `policy.yaml` from the example.
5. Install and run `nats-server` separately.
6. `caesar praetor migrate && caesar praetor serve`.

That's 30–60 minutes for a careful first-timer and a full afternoon
for someone unfamiliar with Python tooling. v1.4's gate question —
*"can someone who isn't me deploy this in 10 minutes?"* — pushes us
past that.

## Decision

CAESAR v1.4 will ship **two first-class install paths** plus a
**config-generator command** that's shared by both.

### 1 — Docker image + reference Compose stack (primary)

- **Image:** `ghcr.io/sinidious/caesar`, tagged `:latest` plus the
  semantic version on each release (`:0.4.0`, `:0.4.1`, …).
  Built multi-arch (`linux/amd64`, `linux/arm64`) so a Raspberry Pi
  is a first-class target.
- **Build:** multi-stage `Dockerfile`. Stage 1 installs the project
  with all default extras (anthropic, openai, ollama, caldav,
  prometheus-client). Stage 2 copies a Python venv into a
  `python:3.11-slim` runtime image. No build toolchain in the final
  image; final size target ~250MB.
- **Workdir:** `/var/lib/caesar` for state (SQLite, NKEYs).
  `WORKDIR` is set there so volume mounts are trivial. UID 10001.
- **Entrypoint:** `caesar praetor serve` by default. `caesar init`,
  `caesar legion new-worker`, etc. remain available via
  `docker run … caesar <subcommand>`.
- **Compose stack** (`docker-compose.yml` shipped in the repo):
  `caesar` + `nats-server` + commented `ollama` + commented
  `searxng`. NATS auth disabled by default (single-host posture
  per ADR-0027). Volume mount for `./var/`. Operator copies the
  file, runs `docker compose up -d`, and has a brain in two
  minutes.

### 2 — pip install (secondary, supported)

`pip install caesar` keeps working exactly as before. The pip path
is what the Docker image uses internally, so there's no two-codepath
maintenance overhead. `pip install caesar[otel]` for tracing,
`pip install caesar[docs]` for the mkdocs site — these extras are
unchanged.

The README emphasises Docker for first-time operators; the
Operations runbook covers both paths.

### 3 — `caesar init` (config generator, shared)

A new top-level CLI subcommand. Writes a self-contained config in
the current directory:

- `.env` with:
  - `CAESAR_DASHBOARD__TOKEN=<64-char URL-safe random>`
  - `CAESAR_DASHBOARD__SIGNING_KEY=<64-char URL-safe random>`
    (per SR-006 — separate from the token)
  - `CAESAR_POLICY__RULES_PATH=./policy.yaml`
  - `CAESAR_LLM__PROVIDER=anthropic` (default; commented
    alternatives for `openai` / `ollama`)
  - Placeholder `CAESAR_LLM__ANTHROPIC__API_KEY=…` the operator
    fills in
- `policy.yaml` seeded with:
  - `allowed_services: [light.turn_on, light.turn_off, light.toggle]`
    (sensible homelab default, commented out by default — operator
    opts in by uncommenting)
  - `allowed_tools: [calculator]` (no creds, no network — safe
    default)
- `./praetor.nkey` — a fresh NKEY seed for the multi-host upgrade
  path. Unused by the single-host default but the operator doesn't
  have to mint one later.
- `./var/` — the directory ADR-0019 expects for the SQLite file.

The command is **idempotent**: re-running on a working install is a
no-op. Overwriting an existing config requires `--force` and prints
a diff of what would change. No interactive prompts (per the
decision in the planning question) — operators looking for a
guided experience use `caesar init --interactive` in a follow-up.

### 4 — Image attestation

Reuses the SR-011 Sigstore build-provenance workflow. The release
job's `actions/attest-build-provenance` step now signs both the
wheel/sdist *and* the multi-arch container image. Operators verify
with the existing `gh attestation verify` flow documented in
`OPERATIONS.md`.

## Alternatives considered

- **Single-arch (amd64) only.** Smaller CI matrix, half the build
  time. Rejected: a Raspberry Pi is the canonical "fresh box" for
  this audience; arm64 is mandatory.
- **Distroless base.** Smaller image, no shell, no
  troubleshooting affordances. Rejected for v1.4 because operators
  *will* need `docker exec` access while debugging their first
  install. `python:3.11-slim` ships `bash` and a few `apt`-installed
  troubleshooting tools.
- **`caesar init --stack`** (the rejected planning option).
  Would orchestrate Compose itself. Rejected: too opinionated;
  doesn't fit homelabs that already run nats-server/Ollama for
  other services. The Compose file in the repo is documentation,
  not a tool that owns the operator's stack.
- **Helm chart.** Kubernetes is interesting but explicitly out of
  the homelab scope. Operators on K3s can write their own; we
  don't ship one.
- **Python `pyinstaller` single binary.** Considered for the
  "no Python on the box" case. Rejected: the Docker image
  already addresses that without giving up the ability to
  `pip install`.
- **`caesar init` writes to `~/.config/caesar/`.** Cross-platform
  config-dir conventions are a rabbit hole and operators running
  in `docker run -v ./var:/var/lib/caesar` need the config beside
  the state. Current-directory wins; XDG support is a follow-up
  if anyone asks.

## Consequences

### Positive

- A new operator on a fresh NUC types four commands: `git clone`,
  edit one line in `.env`, `docker compose up -d`, open
  `http://nuc:8000/dashboard`. Achieves the 10-minute gate.
- pip path keeps working for developers and operators who already
  manage Python services that way; no community fracture.
- Multi-arch image makes Raspberry Pi a first-class deployment
  target — relevant for the homelab demographic.
- Image inherits the existing Sigstore provenance chain; no new
  supply-chain work.
- `caesar init` produces a config that's already SR-006/SR-007
  compliant (separate signing key, 7-day cookie). Bakes the
  security defaults into the install path.

### Negative

- One more CI job: multi-arch image build + push. Slower release
  by ~3 minutes (mostly arm64 emulation under QEMU); acceptable.
- Image size target is 250MB, which is fine for amd64 NUCs but
  meaningful on a Pi over wifi. Mitigated by multi-stage build
  + `.dockerignore` keeping the layer count small.
- Compose file becomes a thing we maintain. Mitigated by it
  being a *reference*, not a tool — operators copy and modify;
  we don't promise upgrades work via `docker compose pull` alone.

### Neutral

- `caesar init` is added under the existing Typer CLI surface.
  Following the `praetor` / `legion` / `db` grouping, it sits at
  the top level (not nested) because operators reach for it
  before they know about the subgroups.
- The CalDAV extras get baked into the default image even though
  not every operator uses them. Avoids the "which extras do I
  need?" trap for new operators. Image-size cost is small (~5MB).
- Default `caesar init` policy uncomments **nothing** for HA —
  the operator decides which services to allow. That's intentional;
  policy choices are the most-rewindable part of the config and
  shouldn't be implicit.

## References

- [ADR-0011](0011-llm-gateway.md) — what extras the image bakes in.
- [ADR-0017](0017-configuration.md) — `.env` layering rules.
- [ADR-0019](0019-sqlite-persistence.md) — why `./var/` exists.
- [ADR-0022](0022-backup-restore.md) — what the operator should
  back up from inside the container.
- [ADR-0024](0024-docs-site-mkdocs.md) — the quickstart page joins
  the docs site nav.
- [ADR-0027](0027-nats-auth-multihost-legion.md) — the NKEY
  `caesar init` mints today is what `caesar legion new-worker`
  would generate later, ready for the operator's first cross-host
  worker.
- [SR-006](../SECURITY-REVIEW.md) — separate signing key; the
  generated `.env` ships it.
- [SR-011](../SECURITY-REVIEW.md) — Sigstore provenance for the
  image.
