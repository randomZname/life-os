"""Trigram index on chunks.text for hybrid RAG retrieval.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-03

Backs the keyword/trigram half of the document hybrid search
(`document_search` in bogi.modules.documents). Additive only: creates a GIN
trigram index on `chunks.text`. `pg_trgm` was already created in 0007; the
`CREATE EXTENSION IF NOT EXISTS` here is idempotent/harmless belt-and-braces.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_text_trgm ON chunks "
        "USING gin (text gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_text_trgm")
