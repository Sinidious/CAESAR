# 0019 — SQLite persistence via SQLAlchemy Core and Alembic

- Status: Accepted
- Date: 2026-05-16
- Deciders: @sinidious

## Context

Three prior ADRs commit CAESAR to a single SQLite file as the durable
store for the brain:

- [ADR-0006](0006-praetor-runtime.md) — LangGraph checkpointer writes
  to SQLite.
- [ADR-0010](0010-memory-hybrid.md) — episodic memory is SQLite;
  semantic memory uses `sqlite-vss` (a SQLite extension) by default.
- [ADR-0012](0012-audit-log.md) — the audit log is a table in the
  same SQLite database.

What none of those ADRs decided is **how** Python code reads and
writes that database. The choices that follow are the kind that touch
every persistence-adjacent module — picking after the first one ships
is a refactor.

Concrete v0.1 needs:

- The audit log writer ([ADR-0012](0012-audit-log.md)) — append-only,
  schema we own and care about, queried by id, time, and decision
  shape.
- The LangGraph checkpointer — schema we *don't* own (LangGraph's
  SQLite checkpointer creates its own tables) and that we should not
  manage with our migration tool.
- The semantic memory loader — needs `sqlite-vss` to load as a
  SQLite extension on connect.

Three quiet constraints make some otherwise-fine choices wrong:

1. **Audit-log table shape is the API.** It's documented, parsed by
   the dashboard, and is the substrate for replay. An ORM that hides
   the SQL behind classes is in the way; we want the table to look
   exactly like the table.
2. **Tests must use the same backend as prod.** No "SQLite in test,
   Postgres in prod" — CAESAR is SQLite by design
   ([ADR-0010](0010-memory-hybrid.md)). That removes one common
   reason teams adopt SQLAlchemy ORM (portability), simplifying our
   choice.
3. **The maintainer is not a full-time platform engineer.** Migrations
   must be runnable with one command and reviewable as plain SQL in
   a PR.

## Decision

CAESAR persists data in SQLite using **SQLAlchemy Core** for queries
and connection management, and **Alembic** for migrations.
Specifically:

- **SQLAlchemy 2.x Core, not ORM.** Tables are declared with
  `sqlalchemy.Table(...)`; reads and writes use
  `select(...)`, `insert(...)`, `update(...)` against a typed
  `Connection`. No `declarative_base`, no mapped classes, no
  `Session`. Result rows are converted to small `pydantic`/dataclass
  records at the edge of `caesar.audit` / `caesar.memory`.
- **One engine, one file, two access modes.**
  - The application code uses **async access via `aiosqlite`**
    behind SQLAlchemy's `create_async_engine("sqlite+aiosqlite://...")`.
  - **Alembic uses sync access** with `sqlite` driver; Alembic does
    not need async. Same URL minus the `+aiosqlite`.
- **WAL mode + `synchronous=NORMAL`** are set on every connection via
  a `connect` event listener. SQLite under default journal mode
  serializes writers in a way that doesn't suit a service.
  `journal_mode=WAL` and `synchronous=NORMAL` are the standard
  service-friendly defaults; the durability tradeoff is acceptable
  for a homelab and is documented in
  [docs/CONFIGURATION.md](../CONFIGURATION.md).
- **`sqlite-vss` is loaded by the same connect listener** when the
  semantic-memory feature is enabled
  ([ADR-0010](0010-memory-hybrid.md)). Loading happens before the
  first query on each new connection.
- **Foreign keys on.** `PRAGMA foreign_keys=ON` in the same listener;
  SQLite ships with them off by default and that is a footgun.
- **Migrations live at `migrations/`** at the repo root (not under
  `src/`, since migrations are not shipped in the wheel). Alembic's
  `env.py` reads the database URL from `caesar.config.Settings`
  ([ADR-0017](0017-configuration.md)), so the same TOML/env config
  that runs the app runs the migrations.
- **One migration per logical change**, each carrying its own SQL.
  Reviewable in a PR; no auto-generation. Alembic's autogenerate is
  fine for ORMs; for Core + a small schema we keep migrations
  hand-written and obvious.
- **LangGraph's checkpointer tables are not in our Alembic history.**
  LangGraph creates them on first connect; we treat them as an
  opaque consumer of the same database file. Alembic's
  `include_object` hook excludes the `langgraph_*` table prefix.
- **`just db-migrate`** and **`just db-revision "name"`** wrap Alembic
  so the maintainer doesn't memorize its CLI.
- **Default DB path** is `${CAESAR_DATA_DIR:-./var}/caesar.sqlite3`,
  overridable per [ADR-0017](0017-configuration.md).

A reference connection-init listener (illustrative):

```python
@event.listens_for(engine.sync_engine, "connect")
def _sqlite_on_connect(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    if settings.memory.vector_backend == "sqlite-vss":
        dbapi_conn.enable_load_extension(True)
        sqlite_vss.load(dbapi_conn)
        dbapi_conn.enable_load_extension(False)
    cursor.close()
```

## Alternatives considered

- **Raw `aiosqlite` + hand-written SQL.** Smallest dependency, full
  control. Loses connection pooling, parameter binding ergonomics,
  Alembic integration, and the small but real safety net of
  SQLAlchemy's `Table` introspection. We adopt SQLAlchemy *only* for
  the parts where it earns its keep (engines, expression language,
  connect-events, Alembic).
- **SQLAlchemy ORM.** Hides the schema behind mapped classes; makes
  the audit table feel like an implementation detail instead of an
  interface. Sessions and unit-of-work patterns are overkill for an
  append-mostly workload.
- **SQLModel.** Sits on SQLAlchemy + pydantic; pleasant API. We're
  already using pydantic at the edges ([ADR-0017](0017-configuration.md))
  but mixing it into the storage layer leaks "this row is a model"
  semantics into a layer we want to stay table-shaped. Reconsider if
  the schema grows enough that mapping classes are pulling weight.
- **Peewee / Tortoise / Pony.** All workable; none have Alembic's
  migration tooling, ecosystem, and review-friendliness.
- **No migrations, schema in code applied on startup.** Tempting for
  v0.1 ("just `CREATE TABLE IF NOT EXISTS ...`"). Stops working the
  first time we change a column type or add a constraint that needs
  a backfill. The cost of Alembic on day one is one folder and one
  recipe; the cost of *not* having it is the first schema change
  after data exists.
- **Postgres from day one.** Right answer for a multi-host service,
  wrong answer for a single-node homelab brain. SQLite already won
  this argument in [ADR-0010](0010-memory-hybrid.md).

## Consequences

### Positive

- One database file, one connection lifecycle, one migration history,
  one backup story — matches the "back up `caesar.sqlite3`, back up
  the brain" promise from [ADR-0012](0012-audit-log.md).
- Audit log SQL is visible and reviewable. So is every migration.
- Async I/O end to end via `aiosqlite` keeps Praetor's event loop
  from blocking on disk.
- `sqlite-vss` loading is centralized in one place, not duplicated
  across modules.
- Foreign keys + WAL + `synchronous=NORMAL` are set by construction;
  no module can forget them.

### Negative

- SQLAlchemy Core has a learning curve for contributors who only know
  ORMs. The surface area we use is small and documented by example
  in `caesar.audit`.
- Migrations are a process step the maintainer must remember (`just
  db-migrate` after pulling). A startup-time check that the database
  is at `alembic_head` and refuses to run otherwise softens this.
- Async SQLite gives us non-blocking I/O but does not parallelize
  writes — SQLite still serializes them. Acceptable for the workload;
  documented so nobody is surprised when a write-heavy test runs at
  the speed of disk.

### Neutral

- Backup/restore tooling (`just db-backup` / `db-restore`) is not in
  this ADR; it's a small follow-up once the schema exists.
- Read replicas, clustering, and write sharding are out of scope.
  SQLite is the answer until it isn't, and when it isn't we get a
  new ADR — not a creeping rewrite of every module.

## References

- [SQLAlchemy 2.0 — Core vs ORM](https://docs.sqlalchemy.org/en/20/tutorial/index.html)
- [SQLAlchemy async — aiosqlite](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#aiosqlite)
- [Alembic](https://alembic.sqlalchemy.org/)
- [sqlite-vss](https://github.com/asg017/sqlite-vss)
- [SQLite WAL mode](https://sqlite.org/wal.html)
- [ADR-0006 — Praetor on FastAPI + LangGraph](0006-praetor-runtime.md)
- [ADR-0010 — Hybrid memory: SQLite + vector store](0010-memory-hybrid.md)
- [ADR-0012 — Audit every brain decision](0012-audit-log.md)
- [ADR-0017 — Configuration via pydantic-settings](0017-configuration.md)
