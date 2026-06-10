"""Memory v1: namespace, importance, summary, source, updated_at.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-29

Adds taxonomy + retrieval-ranking fields to `memories`. Backfill defaults
so existing rows stay valid.
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column("namespace", sa.String(64), nullable=False, server_default="general"),
    )
    op.add_column(
        "memories",
        sa.Column("importance_score", sa.Float(), nullable=False, server_default="0.5"),
    )
    op.add_column(
        "memories",
        sa.Column("summary", sa.String(256), nullable=True),
    )
    op.add_column(
        "memories",
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
    )
    op.add_column(
        "memories",
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.add_column(
        "memories",
        sa.Column("meta", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_memories_user_namespace_active",
        "memories",
        ["user_id", "namespace", "archived"],
    )


def downgrade() -> None:
    op.drop_index("ix_memories_user_namespace_active", table_name="memories")
    op.drop_column("memories", "meta")
    op.drop_column("memories", "updated_at")
    op.drop_column("memories", "source")
    op.drop_column("memories", "summary")
    op.drop_column("memories", "importance_score")
    op.drop_column("memories", "namespace")
