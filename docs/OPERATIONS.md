# Operations

Day-to-day notes for running CAESAR on your own hardware. Architecture
decisions are in the [ADR index](adr/README.md); this file is for the operator.

## Backup and restore

CAESAR keeps all durable state in a single SQLite file (default
`./var/caesar.sqlite3`). See
[ADR-0022](adr/0022-backup-restore.md) for the design.

### Take a backup

Safe while Praetor is running — uses SQLite's Online Backup API.

```sh
caesar db backup --to /path/to/caesar-2026-05-17.sqlite3
```

The destination file is a regular `.sqlite3` snapshot. You can:

- Ship it to another machine via `rsync` / `scp`.
- Hand it to your existing backup tool (Restic, Duplicacy, rsnapshot).
- Open it with `sqlite3` for ad-hoc queries.

By default the command refuses to overwrite an existing file; pass
`--overwrite` if that's what you want.

### Restore from a backup

**Stop Praetor first.** Restoring while the service is running is
undefined.

```sh
# Verify the source looks valid first (the command does this too):
sqlite3 /path/to/caesar-2026-05-17.sqlite3 "PRAGMA integrity_check;"

# Then restore:
caesar db restore --from /path/to/caesar-2026-05-17.sqlite3 --force
```

The command runs an integrity check on the source and refuses if the
file doesn't have CAESAR's schema (i.e. no `audit_log` table). It
won't overwrite the live DB unless you pass `--force`.

After a restore, start Praetor again. The WAL / SHM sidecar files
will be recreated on first open.

### Scheduling

There is no built-in scheduler — the operator runs `caesar db backup`
from cron, a systemd timer, or whatever else they already use:

```cron
# Nightly at 03:30; keep 14 days of dated snapshots.
30 3 * * *  caesar db backup --to "/var/backups/caesar/caesar-$(date +\%Y-\%m-\%d).sqlite3" --overwrite
```

```ini
# Or as a systemd timer:
[Unit]
Description=Nightly CAESAR backup

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

Pair with whatever rotation policy your storage tier prefers. The
SQLite snapshots compress well with `gzip` / `zstd`.

### What's *not* in scope

- **Continuous WAL replication.** Litestream or similar would give
  you near-zero data loss; it's deferred to a follow-up ADR.
- **Automated restore on corruption.** CAESAR doesn't try to be
  clever here. If something looks wrong, you decide.
- **Cross-engine portability.** Backups are SQLite-specific. If a
  future ADR brings Postgres in, that's a separate path.

## Metrics

Praetor exposes Prometheus metrics at `GET /metrics`. There is no
auth on this route: bind on loopback and front it with a reverse
proxy, or scrape from the host running Praetor.

What you get:

- `caesar_audit_events_total{event_type}` — Counter. Every audit row
  increments this. Pivot by event_type for chat, service calls,
  policy denials, retention sweeps, etc.
- `caesar_chat_duration_seconds` — Histogram. End-to-end `/v1/chat`
  latency.
- `caesar_workers_registered` — Gauge. Currently registered Legion
  workers.
- `caesar_retention_sweeper_running` — Gauge. 1 when the background
  retention sweep is alive, 0 when it isn't.
- `caesar_semantic_indexer_running` — Gauge. Same for the semantic
  indexer.
- `caesar_audit_bus_subscribers` — Gauge. Live SSE subscribers on
  the audit bus (dashboard tabs).

Example Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: caesar
    static_configs:
      - targets: ['127.0.0.1:8000']
    metrics_path: /metrics
    scrape_interval: 30s
```


## Tracing

CAESAR can emit OpenTelemetry traces. The SDK is an opt-in extra
(ADR-0023) so the default install stays lean for users who don't run
a collector.

```sh
pip install 'caesar[otel]'
```

When the extra is installed, tracing turns itself on at startup. Point
Praetor at any OTLP/HTTP-compatible backend with the standard OTel env
vars:

```sh
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_SERVICE_NAME=caesar-praetor   # defaults to this anyway
```

To turn tracing off temporarily without uninstalling, set
`OTEL_SDK_DISABLED=true`.

What you get on a `/v1/chat` request:

- A FastAPI server span for the HTTP request.
- A `brain.node.call_llm` span per iteration of the brain graph.
- A `brain.node.dispatch_tools` span when the model invoked tools,
  with one child `brain.tool` span per tool call.
- An `llm.complete` span around the Anthropic SDK call with GenAI
  semantic-convention attributes (`gen_ai.system`,
  `gen_ai.request.model`, `gen_ai.usage.input_tokens`,
  `gen_ai.usage.output_tokens`).
- SQLAlchemy spans for every audit/memory query that ran in-band.

Default sampler is `ParentBased(AlwaysOn)` — fine for homelab QPS.
Override with the spec's env vars
(`OTEL_TRACES_SAMPLER=parentbased_traceidratio` +
`OTEL_TRACES_SAMPLER_ARG=0.1`) if you start fronting public endpoints.

## Network exposure

Praetor binds **`127.0.0.1:8000`** by default. A fresh install is
only reachable from the host running it. Everything below is the
operator's deliberate choice:

```sh
# Expose on the LAN. Front it with auth (reverse proxy + basic
# auth, or a VPN). /v1/* is unauthenticated at the HTTP layer.
export CAESAR_SERVER__HOST=0.0.0.0

# Or bind to a specific NIC.
export CAESAR_SERVER__HOST=10.0.0.5
```

See [SECURITY-REVIEW.md](SECURITY-REVIEW.md) — gap SR-001 explains
the trade-off and how it's mitigated by the loopback default.

## Verifying a release

Every published release ships a wheel and an sdist, both signed via
GitHub's [Sigstore build provenance](https://docs.github.com/en/actions/security-guides/using-artifact-attestations-to-establish-provenance-for-builds)
attestation (SR-011). Operators who care about supply-chain hygiene
can verify a downloaded artifact came from this repo's CI:

```sh
# After downloading caesar-0.X.0-py3-none-any.whl from the GitHub Release:
gh attestation verify caesar-0.X.0-py3-none-any.whl \
    --repo Sinidious/CAESAR
```

The verifier confirms the artifact's digest, the workflow that built
it, and the repo / ref it was built from. Failure means either the
file is tampered with, was built outside this repo, or your local
`gh` CLI is too old (needs 2.49+).

## Where the data lives

- `./var/caesar.sqlite3` — the durable store.
- `.cache/` — local-only test/dev artefacts; safe to delete.
- `./var/caesar.sqlite3-wal` and `-shm` — SQLite write-ahead log
  sidecars. **Always include them in `cp`-style backups** (or use
  `caesar db backup`, which handles it for you).
