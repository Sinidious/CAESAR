# Operations

Day-to-day notes for running CAESAR on your own hardware. Architecture
decisions are in [docs/adr/](adr/); this file is for the operator.

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

## Where the data lives

- `./var/caesar.sqlite3` — the durable store.
- `.cache/` — local-only test/dev artefacts; safe to delete.
- `./var/caesar.sqlite3-wal` and `-shm` — SQLite write-ahead log
  sidecars. **Always include them in `cp`-style backups** (or use
  `caesar db backup`, which handles it for you).
