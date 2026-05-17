# 0022 — Backup and restore via SQLite Online Backup API

- Status: Accepted
- Date: 2026-05-17
- Deciders: @sinidious

## Context

CAESAR keeps its entire durable state in one SQLite file
([ADR-0019](0019-sqlite-persistence.md)) — audit log
([ADR-0012](0012-audit-log.md)), semantic chunks
([ADR-0010](0010-memory-hybrid.md)), operator settings (PR #35),
LangGraph checkpoints ([ADR-0006](0006-praetor-runtime.md)). The
v1.0 roadmap calls for documented backup/restore; an operator who
can't recover from "the disk died" doesn't have a daily-driver.

The naive answer is "copy the file." That isn't quite right while
Praetor is running: WAL mode means the live `.sqlite3` file is missing
data that lives in a sibling `-wal` and `-shm`. A `cp` mid-write can
yield an inconsistent snapshot.

## Decision

CAESAR ships `caesar db backup` and `caesar db restore` CLI commands
that use **SQLite's Online Backup API** (`sqlite3.Connection.backup`).
Specifically:

- **Backup** is hot-safe. The CLI opens the live DB read-only,
  opens the destination file, and calls `src.backup(dst)`. SQLite
  serialises the backup against any concurrent writes; the dest is a
  point-in-time snapshot.
- **Restore** is *not* hot-safe. The CLI refuses unless the operator
  passes `--force` (and Praetor isn't running on the dest). The
  source is verified first: `PRAGMA integrity_check = ok` and the
  presence of our `audit_log` table. We then call `src.backup(dst)`
  with the source playing the source role, overwriting the live file.
- **Format** is a plain SQLite file. Operators can `sqlite3 caesar.bak`
  it for ad-hoc queries, ship it through their normal backup tooling
  (Restic, Duplicacy, rsnapshot), or just `mv` it back into place
  manually if they prefer.
- **No SQL dumps.** They look human-readable but reintroduce the
  schema-drift problem (a dump from version N may not restore cleanly
  into version N+1). Binary backups roundtrip a known shape.
- **Scope** is SQLite only. If we ever support Postgres, that gets a
  separate ADR — the workflow is different.

## Alternatives considered

- **File copy (`cp` / `shutil.copy`)** — Wrong while Praetor runs.
  Could be made correct with `sqlite3 .backup` first then copy, but
  that's strictly worse than calling the API directly.
- **`VACUUM INTO`** — Works, produces a clean snapshot. Slightly more
  awkward for restore (no "live in, file out" symmetry); same hot-
  safety as the Online Backup API. Could substitute for backup; not
  for restore. Not chosen because the Online Backup API matches both
  sides of the workflow.
- **Continuous WAL streaming (Litestream)** — Strictly better for
  recovery objective (minutes of data loss instead of "since last
  backup"), strictly more setup. A future ADR may bring it in once
  the operator pain justifies it. For v1.0 the answer is: schedule
  `caesar db backup` from cron / systemd timer and ship the file
  somewhere durable.
- **JSON / SQL dump export** — Loses indexes, requires schema match
  on restore. Useful for cross-version migrations, not for routine
  backups.

## Consequences

### Positive

- Recovery story is one command + one file. No clustered services,
  no per-table dump scripts.
- Hot backup means operators can schedule it without coordinating
  with chat traffic.
- Verification before restore catches the "I pointed at the wrong
  file" mistake.

### Negative

- RPO is "since last backup." Streaming WAL replication (Litestream)
  is a follow-up when continuous recovery matters.
- Backups grow with the DB. The retention sweep
  ([ADR-0020](0020-memory-retention-ttl.md)) bounds the on-disk size,
  but backups still scale with that bound.
- Restore is operator-driven and offline. We do not auto-restore on
  detection of a corrupt DB; the operator decides.

### Neutral

- Whether the `-wal` / `-shm` sidecar files exist after restore is
  SQLite's call. They'll be recreated on the next open.

## References

- [SQLite Online Backup API](https://sqlite.org/c3ref/backup_finish.html)
- [Python `sqlite3.Connection.backup`](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)
- [Litestream](https://litestream.io/)
- [ADR-0019 — SQLite persistence](0019-sqlite-persistence.md)
