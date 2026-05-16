"""Top-level CLI (Typer).

Subcommands are grouped by subsystem so the surface scales as more of
the system lands:

    caesar praetor serve     # start the brain HTTP service
    caesar praetor migrate   # apply schema migrations
"""

from __future__ import annotations

from typing import Annotated

import typer
import uvicorn

from caesar.config import get_settings
from caesar.log import configure_logging

app = typer.Typer(help="CAESAR command-line interface.", no_args_is_help=True)

praetor_app = typer.Typer(help="Praetor brain commands.", no_args_is_help=True)
app.add_typer(praetor_app, name="praetor")


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


if __name__ == "__main__":  # pragma: no cover
    app()
