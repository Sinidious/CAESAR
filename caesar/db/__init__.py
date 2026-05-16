"""Persistence layer (ADR-0019).

SQLAlchemy Core (not ORM) for queries; Alembic for migrations. One
SQLite file, async I/O via aiosqlite, sync access for migrations.
"""
