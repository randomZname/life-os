"""Life-OS domains: people/interactions, transactions, captures, monitors, habits.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-07

Additive only — seven new tables for the personal-life ("Jarvis") domains beyond
university: personal CRM (people + interactions), money/tutoring (transactions),
universal capture inbox (captures), persistent watchers (monitors), and habit
tracking (habits + habit_logs). No changes to existing tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=True),
        sa.Column("relation", sa.String(64), nullable=True),
        sa.Column("birthday", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_contact_at", sa.DateTime(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_people_user_archived", "people", ["user_id", "archived"])

    op.create_table(
        "interactions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("channel", sa.String(32), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
    )
    op.create_index("ix_interactions_person_time", "interactions", ["person_id", "occurred_at"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="BGN"),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="SET NULL"), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_transactions_user_time", "transactions", ["user_id", "occurred_at"])

    op.create_table(
        "captures",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="note"),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="inbox"),
        sa.Column("routed_to", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_captures_user_status", "captures", ["user_id", "status"])

    op.create_table(
        "monitors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="webpage"),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("rule", sa.Text(), nullable=True),
        sa.Column("last_value", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_monitors_user_active", "monitors", ["user_id", "active"])

    op.create_table(
        "habits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("schedule", sa.String(64), nullable=True),
        sa.Column("target", sa.String(64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_habits_user_active", "habits", ["user_id", "active"])

    op.create_table(
        "habit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("habit_id", sa.Integer(), sa.ForeignKey("habits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column("value", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("habit_id", "log_date", name="uq_habit_log_date"),
    )


def downgrade() -> None:
    op.drop_table("habit_logs")
    op.drop_index("ix_habits_user_active", table_name="habits")
    op.drop_table("habits")
    op.drop_index("ix_monitors_user_active", table_name="monitors")
    op.drop_table("monitors")
    op.drop_index("ix_captures_user_status", table_name="captures")
    op.drop_table("captures")
    op.drop_index("ix_transactions_user_time", table_name="transactions")
    op.drop_table("transactions")
    op.drop_index("ix_interactions_person_time", table_name="interactions")
    op.drop_table("interactions")
    op.drop_index("ix_people_user_archived", table_name="people")
    op.drop_table("people")
