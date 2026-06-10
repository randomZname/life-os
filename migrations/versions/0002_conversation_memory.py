"""Conversation memory: threads + turns (shared across channels per user).

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-15
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_threads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_until_turn_id", sa.Integer(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_conv_threads_user_active",
        "conversation_threads",
        ["user_id", "archived"],
    )

    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "thread_id",
            sa.Integer(),
            sa.ForeignKey("conversation_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("output_text", sa.Text(), nullable=False),
        sa.Column("pydantic_messages_json", sa.LargeBinary(), nullable=False),
        sa.Column("pydantic_messages_compact", sa.LargeBinary(), nullable=False),
        sa.Column("usage_tokens_input", sa.Integer(), nullable=True),
        sa.Column("usage_tokens_output", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_conv_turns_thread_created",
        "conversation_turns",
        ["thread_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conv_turns_thread_created", table_name="conversation_turns")
    op.drop_table("conversation_turns")
    op.drop_index("ix_conv_threads_user_active", table_name="conversation_threads")
    op.drop_table("conversation_threads")
