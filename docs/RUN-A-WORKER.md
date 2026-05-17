# Run a worker on another box

CAESAR's brain (Praetor) and its workers (Legion) talk over NATS.
Through v1.1 the bus was unauthenticated single-host;
[ADR-0027](adr/0027-nats-auth-multihost-legion.md) opens it up so
workers can run on different machines with authenticated, scoped
access.

This page walks through standing up your second box.

## What you'll need

- A NATS server reachable from both hosts (the same `nats-server`
  process Praetor already talks to is fine).
- Network connectivity on TCP/4222 between the two hosts.
- The `caesar` CLI installed on both.

## 1 — Mint identities

NKEYs are ed25519 keypairs NATS recognises natively. Each
identity (Praetor, plus every worker) gets its own.

On any machine with the CAESAR CLI installed:

```sh
caesar legion new-worker --name praetor
caesar legion new-worker --name kitchen-pi      # one per worker host
```

Each invocation prints:

- The **seed** (`SUA…`) — give this to the matching host and
  nothing else.
- The **public key** (`U…`) — paste into `nats-server.conf`.
- A copy-pasteable `authorization.users` snippet.
- The env vars to set on the worker host.

Treat the seed like a password — it never goes into git, chat,
screenshots, or `nats-server.conf` itself.

## 2 — Configure `nats-server`

Start from `examples/legion-multihost-nats.conf` in the repo. Replace
the placeholder public keys with the ones you just minted.

**Important:** NATS rejects `user:` alongside `nkey:` —
NKEY identities are matched by the public key alone. The
human-friendly worker label goes in a comment above each entry:

```hocon
# caesar-worker-<name>
{ nkey: "U…",
  permissions: {
    publish:   { allow: ["caesar.registry.hello",
                         "caesar.registry.heartbeat",
                         "caesar.reply.<name>.>"] },
    subscribe: { allow: ["caesar.dispatch.>",
                         "caesar.reply.<name>.>"] },
    # NATS request/reply uses a temporary _INBOX.> subject;
    # allow_responses grants the worker a one-shot publish
    # permission on it when answering a Praetor request.
    allow_responses: true,
  },
}
```

Praetor's permissions are wider — it owns the orchestration layer:

```hocon
# caesar-praetor
{ nkey: "U…",
  permissions: {
    publish:   { allow: ["caesar.>"] },
    subscribe: { allow: ["caesar.>"] },
  },
}
```

Reload (or restart) `nats-server` after editing.

## 3 — Wire Praetor to its NKEY

On the host running Praetor, save the seed somewhere only root can
read:

```sh
sudo install -m 0600 -o root -g root praetor.nkey /etc/caesar/praetor.nkey
```

Then set the env vars (in your `.env`, systemd unit, or wherever
you configure CAESAR):

```sh
CAESAR_BUS__ENABLED=true
CAESAR_BUS__URL=nats://<your-nats-host>:4222
CAESAR_BUS__AUTH__ENABLED=true
CAESAR_BUS__AUTH__NKEY_SEED_PATH=/etc/caesar/praetor.nkey
```

Restart Praetor. The startup log should say
`bus.connected authenticated=true`.

## 4 — Wire the worker

The exact wiring depends on which worker you're running.
For the memory-recall worker shipped with CAESAR
(`caesar.legion.memory_recall.MemoryRecallWorker`) the env shape is
the same on the worker host — save the seed and export:

```sh
CAESAR_BUS__ENABLED=true
CAESAR_BUS__URL=nats://<your-nats-host>:4222
CAESAR_BUS__AUTH__ENABLED=true
CAESAR_BUS__AUTH__NKEY_SEED_PATH=/etc/caesar/kitchen-pi.nkey
```

Then start the worker process (a `python -m` entry point or your
own service unit; the worker module exposes a `main()`).

## 5 — Confirm

Two things should change in Praetor's `/dashboard/agents` view:

- The worker shows up as **online** within a few seconds of
  startup (the `registry.hello` event).
- A heartbeat ticks every ~30s.

`/metrics` reflects the same: `caesar_workers_registered` becomes
the new count.

If the worker can't connect, three usual suspects:

1. **Wrong NKEY.** The worker's seed must derive the public key
   you pasted into `nats-server.conf`. A typo in either rejects
   the connection at challenge-response time.
2. **Wrong subject permissions.** Tail the nats-server logs;
   permission errors print `Subscription Forbidden` /
   `Publish Forbidden` with the offending subject.
3. **Firewall.** NATS on 4222 needs to reach the worker host. Add
   the rule before assuming it's a CAESAR problem.

## TLS

NKEYs are challenge-response, so the seed itself doesn't traverse
the network even over plaintext NATS. Message *contents* do. On
an untrusted LAN or across the open internet, terminate TLS at
`nats-server` (uncomment the `tls { … }` block in the example
config and point at certs you provisioned with your tool of
choice).

## When things break

- See the [security review's SR-009 row](SECURITY-REVIEW.md) for
  the threat model.
- For the cross-host *design*, read
  [ADR-0027](adr/0027-nats-auth-multihost-legion.md).
- For single-host troubleshooting, the
  [Operations runbook](OPERATIONS.md) is the right starting
  point.
