"""semantic_chunks for vector recall

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "semantic_chunks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("audit_log_id", sa.Integer, nullable=False),
        sa.Column("text", sa.String, nullable=False),
        sa.Column("embedding", sa.JSON, nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("audit_log_id", name="uq_semantic_chunks_audit_log_id"),
    )
    op.create_index(
        "ix_semantic_chunks_audit_log_id", "semantic_chunks", ["audit_log_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_semantic_chunks_audit_log_id", "semantic_chunks")
    op.drop_table("semantic_chunks")
