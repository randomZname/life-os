"""Enforce at most one active (non-archived) conversation thread per user.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-15
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # If any user has more than one non-archived thread (race-created earlier),
    # archive all but the highest-id so the unique index can be created.
    op.execute(
        """
        UPDATE conversation_threads ct
        SET archived = true
        WHERE archived = false
          AND id < (
            SELECT MAX(id) FROM conversation_threads
            WHERE user_id = ct.user_id AND archived = false
          )
        """
    )
    op.create_index(
        "ux_conv_threads_one_active",
        "conversation_threads",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("archived = false"),
    )


def downgrade() -> None:
    op.drop_index("ux_conv_threads_one_active", table_name="conversation_threads")
