"""Long-term memory store with embeddings.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-15
"""

from __future__ import annotations

from typing import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

from bogi.config import settings

# revision identifiers
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="fact"),
        sa.Column("source_turn_id", sa.Integer(), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(settings.embedding_dimension), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_memories_user_archived", "memories", ["user_id", "archived"])
    op.execute(
        "CREATE INDEX memories_embedding_idx ON memories "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS memories_embedding_idx")
    op.drop_index("ix_memories_user_archived", table_name="memories")
    op.drop_table("memories")
