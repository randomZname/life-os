"""Approval queue: external-write actions awaiting user OK.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-31

Adds the `approvals` table backing V2 §2.B (decision D-007/D-012). No data
backfill needed — new table.
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("preview", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("request_key", sa.String(64), nullable=True),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_approvals_user_status", "approvals", ["user_id", "status"])
    op.create_index("ix_approvals_request_key", "approvals", ["request_key"])


def downgrade() -> None:
    op.drop_index("ix_approvals_request_key", table_name="approvals")
    op.drop_index("ix_approvals_user_status", table_name="approvals")
    op.drop_table("approvals")
