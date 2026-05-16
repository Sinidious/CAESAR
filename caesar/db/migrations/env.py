"""Alembic environment.

Online mode only — we don't generate offline SQL scripts. The URL is
injected programmatically by :mod:`caesar.db.migrate`, not read from
an ini file.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from caesar.db.schema import metadata

config = context.config
target_metadata = metadata


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
