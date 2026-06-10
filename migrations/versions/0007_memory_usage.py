"""Memory usage tracking + hybrid-search indexes.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-01

Makes `memories` usage-aware (last_accessed_at, access_count) and adds the
indexes backing faster vector + keyword/hybrid retrieval. Additive only:
backfills existing rows via server_default. No drops of existing columns.
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column("last_accessed_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "memories",
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memories_embedding_hnsw ON memories "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memories_content_trgm ON memories "
        "USING gin (content gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memories_content_trgm")
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding_hnsw")
    op.drop_column("memories", "access_count")
    op.drop_column("memories", "last_accessed_at")
