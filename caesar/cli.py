"""Top-level CLI (Typer).

Subcommands are grouped by subsystem so the surface scales as more of
the system lands:

    caesar praetor serve     # start the brain HTTP service
    caesar praetor migrate   # apply schema migrations
    caesar memory sweep      # one-shot retention sweep
    caesar db backup         # hot-safe SQLite snapshot
    caesar db restore        # replace the live DB with a snapshot
    caesar legion new-worker # mint an NKEY for a new Legion worker
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from caesar.config import get_settings
from caesar.log import configure_logging

app = typer.Typer(help="CAESAR command-line interface.", no_args_is_help=True)

praetor_app = typer.Typer(help="Praetor brain commands.", no_args_is_help=True)
app.add_typer(praetor_app, name="praetor")

memory_app = typer.Typer(help="Episodic-memory maintenance commands.", no_args_is_help=True)
app.add_typer(memory_app, name="memory")

db_app = typer.Typer(help="Database maintenance commands.", no_args_is_help=True)
app.add_typer(db_app, name="db")

legion_app = typer.Typer(help="Legion (worker pool) commands.", no_args_is_help=True)
app.add_typer(legion_app, name="legion")


@praetor_app.command("serve")
def praetor_serve(
    host: Annotated[
        str | None,
        typer.Option(help="Bind host. Overrides CAESAR_SERVER__HOST."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(help="Bind port. Overrides CAESAR_SERVER__PORT."),
    ] = None,
    reload: Annotated[
        bool,
        typer.Option(help="Auto-reload on code change (dev only)."),
    ] = False,
) -> None:
    """Run the Praetor FastAPI service under uvicorn."""

    settings = get_settings()
    configure_logging(settings.log)

    uvicorn.run(
        "caesar.praetor.app:create_app",
        host=host if host is not None else settings.server.host,
        port=port if port is not None else settings.server.port,
        reload=reload,
        factory=True,
        log_config=None,
    )


@praetor_app.command("migrate")
def praetor_migrate() -> None:
    """Apply outstanding Alembic migrations to the configured database."""

    from caesar.db.migrate import upgrade_to_head

    settings = get_settings()
    configure_logging(settings.log)
    upgrade_to_head(settings.db.url)


@memory_app.command("sweep")
def memory_sweep(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Count what would be deleted; don't touch the DB."),
    ] = False,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Actually delete rows older than the TTL."),
    ] = False,
    days: Annotated[
        int | None,
        typer.Option(help="Override CAESAR_MEMORY__RETENTION_DAYS for this sweep."),
    ] = None,
) -> None:
    """Run a one-shot retention sweep against the configured database."""

    import asyncio

    from caesar.db.audit import AuditLogger
    from caesar.db.engine import create_engine
    from caesar.memory.retention import sweep_once

    if dry_run == apply:
        raise typer.BadParameter("specify exactly one of --dry-run or --apply")

    settings = get_settings()
    configure_logging(settings.log)
    retention_days = days if days is not None else settings.memory.retention_days

    async def _run() -> None:
        engine = create_engine(settings.db.url, echo=settings.db.echo)
        try:
            audit = AuditLogger(engine)
            result = await sweep_once(
                engine,
                retention_days=retention_days,
                dry_run=dry_run,
                audit=audit,
            )
            verb = "would delete" if result.dry_run else "deleted"
            typer.echo(f"{verb} {result.deleted} row(s) older than {result.cutoff.isoformat()}")
        finally:
            await engine.dispose()

    asyncio.run(_run())


@db_app.command("backup")
def db_backup(
    to: Annotated[
        Path,
        typer.Option("--to", help="Destination .sqlite3 path."),
    ],
    overwrite: Annotated[
        bool,
        typer.Option(help="Overwrite if the destination already exists."),
    ] = False,
) -> None:
    """Take a hot-safe snapshot of the live SQLite database."""

    from caesar.db.backup import BackupError, backup_to

    settings = get_settings()
    configure_logging(settings.log)
    try:
        dest = backup_to(settings.db.url, to, overwrite=overwrite)
    except BackupError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Backed up {settings.db.url} → {dest}")


@db_app.command("restore")
def db_restore(
    source: Annotated[
        Path,
        typer.Option("--from", help="Source backup .sqlite3 file."),
    ],
    force: Annotated[
        bool,
        typer.Option(help="Overwrite the live DB. Stop Praetor first."),
    ] = False,
) -> None:
    """Replace the live SQLite database with a verified backup."""

    from caesar.db.backup import BackupError, restore_from

    settings = get_settings()
    configure_logging(settings.log)
    try:
        dest = restore_from(settings.db.url, source, force=force)
    except BackupError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Restored {source} → {dest}")


@legion_app.command("new-worker")
def legion_new_worker(
    name: Annotated[
        str,
        typer.Option(help="Short identifier used in the nats-server config block."),
    ],
) -> None:
    """Mint a fresh NKEY identity for a new Legion worker (ADR-0027).

    Prints the seed (save it on the worker host), the public key
    (paste it into ``nats-server.conf``), a copy-pasteable
    ``authorization.users`` block, and the env vars the worker
    process needs to authenticate.
    """

    from caesar.legion.bootstrap import generate_worker_identity

    identity = generate_worker_identity(name=name)
    typer.echo(identity.format_for_operator())


if __name__ == "__main__":  # pragma: no cover
    app()
