# 0027 — NATS auth and multi-host Legion

- Status: Accepted
- Date: 2026-05-17
- Deciders: @Sinidious
- Related issues / PRs: v1.2 milestone (after [ADR-0009](0009-message-bus-nats.md));
  closes SECURITY-REVIEW.md row SR-009.

## Context

[ADR-0009](0009-message-bus-nats.md) picked NATS as CAESAR's message
bus. v0.3 through v1.1 shipped it as a single-node `127.0.0.1:4222`
process with no auth, no TLS, and no operational story for running
workers on a different machine from Praetor.

That was the right call for v0.3 — when the only worker was an
in-process memory-recall handler, a credentialed bus would have been
ceremony for no benefit. v1.2's gate question changes the picture:
*can a worker run on a different box?* That requires three things
the current bus doesn't have:

1. **Authentication** that survives the LAN. SR-009 in the security
   review explicitly named this as the open work.
2. **Authorisation per identity** so a compromised worker can't
   impersonate Praetor or another worker.
3. **A bootstrap story** so the operator can stand up a new worker
   on a fresh machine without copy-pasting plaintext secrets around.

NATS supports several auth mechanisms (user/password, NKEY, JWT
accounts, mTLS). The decision is which to commit to.

## Decision

CAESAR v1.2 adds **NKEY-per-identity** NATS auth to the Legion bus.

- Each participant — Praetor and every Legion worker — gets its own
  ed25519 NKEY seed. The public key is listed in
  `nats-server.conf`'s `authorization.users` block with a scoped
  `permissions` clause; the seed file lives on the host running that
  identity and nowhere else.
- Authorisation is **subject-scoped per identity**:
  - Praetor may publish/subscribe on the full `caesar.>` namespace
    (it's the orchestrator).
  - Each worker may *publish* only on its own reply subject
    (`caesar.reply.<worker-id>.>`) and *subscribe* only to the
    capability subjects it implements
    (`caesar.dispatch.<capability>`).
  - Registry hellos (`caesar.registry.hello`) and heartbeats
    (`caesar.registry.heartbeat`) are open for publish to all
    workers but only Praetor can subscribe.
- **TLS is optional.** Operators on a trusted LAN can run plaintext
  NATS and the design still holds (NKEYs are challenge-response so
  they don't leak the seed even over plaintext, though that of
  course doesn't protect message contents). Operators going over an
  untrusted network must enable TLS in `nats-server.conf` —
  documented but not required at the protocol layer.
- **Auth is opt-in.** Existing single-host deployments running
  `CAESAR_BUS__ENABLED=true` with no auth keep working. A new
  `CAESAR_BUS__AUTH__ENABLED` flag (default `false`) and
  `CAESAR_BUS__AUTH__NKEY_SEED_PATH` / `CAESAR_BUS__AUTH__USER` env
  vars wire the credentials. When `auth.enabled=false`, the bus
  connects without credentials exactly as before.

The `caesar.bus.client.Bus` wrapper accepts the auth fields and
hands them to `nats-py`'s `Client.connect(nkeys_seed=...)`. Workers
construct their own `Bus` with their own seed; Praetor uses its
seed when it builds the registry.

### What we ship

- A new `BusAuthSettings` Pydantic model in `caesar.config`
  exposing `enabled`, `nkey_seed_path`, and (for tests) optional
  `nkey_seed`.
- `caesar.bus.client.Bus` learns the auth fields and passes them
  through. The connection error surface unchanged for unauth'd use.
- An `examples/legion-multihost-nats.conf` operator-curated
  reference config showing one Praetor identity + one worker
  identity, both NKEY-signed, with per-identity `permissions`.
- A `scripts/legion-new-worker.py` (or equivalent in `just`) that
  generates a fresh NKEY pair and prints the
  `nats-server.conf` snippet plus the env vars the new worker
  needs.
- Documentation: `docs/RUN-A-WORKER.md` walks through standing up
  a worker on a second machine.

### What we don't ship in v1.2

- **JWT/account-scoped auth.** Easy to migrate to from NKEY if the
  worker count ever grows past what static config can stomach;
  deferred until that's a real problem.
- **Automatic credential issuance** (Praetor mints worker
  credentials). Possible follow-up; v1.2 stays static so the
  operator's credential boundary is obvious.
- **Federation / leaf nodes.** Single NATS cluster (one or more
  nodes) is the v1.2 picture. Multi-cluster topology is a
  different ADR.

## Alternatives considered

- **Username + password from config file.** Familiar, but secrets
  live in plaintext alongside permissions and rotation is a
  config edit. NKEYs are asymmetric, NATS-native, and the public
  half is what's in config — the seed never leaves the host that
  uses it. Strictly better for the same operational cost.
- **Decentralized JWT / NATS Accounts.** Excellent for fleets of
  workers and signed token revocation, but introduces an extra
  signing role, account separation, and a CLI (`nsc`) the operator
  has to learn. Overkill until CAESAR is being run in a context
  with more than a handful of workers.
- **mTLS client certs.** Reuses an existing PKI nicely if you have
  one; otherwise the operator now has a private CA to maintain.
  Cert rotation operational burden CAESAR doesn't otherwise need.
- **Do nothing; keep no-auth localhost.** Permanent ceiling at
  single-host. The whole *Legion* name implies plurality; ducking
  this would be a strategic regression.

## Consequences

### Positive

- Workers can run on different boxes. The architecture the project
  was named for becomes deployable.
- SR-009 closes. Cross-host bus traffic is authenticated and
  subject-scoped.
- The opt-in flag keeps the single-host story (existing operators'
  setup) working unchanged — no upgrade pressure.
- NKEY-based auth is well-documented in NATS itself; we ride the
  upstream maturity.

### Negative

- One more knob (`CAESAR_BUS__AUTH__*`) in the env surface.
  Mitigated by it being optional and default-off.
- Operator must hand-roll their `nats-server.conf` (we ship an
  example, not a tool that owns the file). NATS already has good
  docs here; CAESAR doesn't try to be a NATS distribution.
- Subject scoping is per-identity hand-written for now. If the
  permissions grammar ever gets unwieldy we generate it; v1.2
  expects ~5 identities.

### Neutral

- Bus tests in CI still run with auth disabled because the single
  in-process worker has no host boundary to defend. Authenticated
  tests are added as separate cases gated on `nats-server` being
  on `PATH`.
- `examples/legion-multihost-nats.conf` is operator reference
  documentation; CI doesn't lint it.
- `nats-py` already supports `nkeys_seed` and credential files; no
  upstream dependency change.

## References

- [ADR-0009](0009-message-bus-nats.md) — bus choice.
- [ADR-0013](0013-policy-engine.md) — the auth boundary CAESAR
  already enforces above the bus; this ADR extends it downward.
- [SECURITY-REVIEW.md](../SECURITY-REVIEW.md) — gap SR-009.
- [NATS auth — NKEYs](https://docs.nats.io/running-a-nats-service/configuration/securing_nats/auth_intro/nkey_auth)
- [`nats-py` credentials](https://nats-io.github.io/nats.py/#using-tls-and-authentication)
