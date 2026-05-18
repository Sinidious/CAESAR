"""personal_facts + memory_extract_cursor tables for v1.8 (ADR-0033)

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "personal_facts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(128), nullable=False, unique=True),
        sa.Column("value", sa.String, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_audit_id", sa.Integer, nullable=True),
    )
    op.create_index(
        "idx_personal_facts_key",
        "personal_facts",
        ["key"],
        unique=True,
    )
    op.create_index(
        "idx_personal_facts_last_confirmed",
        "personal_facts",
        ["last_confirmed_at"],
    )

    op.create_table(
        "memory_extract_cursor",
        # Singleton table — id=1 always. last_audit_id is the highest
        # audit_log id the extractor has consumed.
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("last_audit_id", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("memory_extract_cursor")
    op.drop_index("idx_personal_facts_last_confirmed", table_name="personal_facts")
    op.drop_index("idx_personal_facts_key", table_name="personal_facts")
    op.drop_table("personal_facts")
