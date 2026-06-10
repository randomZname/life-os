"""Database schema for BogiAgent."""

from __future__ import annotations

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from bogi.config import settings


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fmi_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    documents: Mapped[list[Document]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=True,
    )
    file_path: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    course: Mapped[Course | None] = relationship(back_populates="documents")
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_idx", name="uq_chunk_doc_idx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dimension),
        nullable=True,
    )

    document: Mapped[Document] = relationship(back_populates="chunks")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConversationThread(Base):
    """One active thread per user. Shared across Telegram + Moodle channels.

    `/нов разговор` archives the active thread and creates a new one.
    """

    __tablename__ = "conversation_threads"
    __table_args__ = (
        Index("ix_conv_threads_user_active", "user_id", "archived"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_until_turn_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    turns: Mapped[list[ConversationTurn]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="ConversationTurn.id",
    )


class Memory(Base):
    """Long-term memory: curated facts about the user/project/preferences.

    NOT a transcript — these are stable nuggets the agent (or user) decided to retain.
    Retrieved by semantic similarity + namespace + importance + recency each turn.
    """

    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_user_archived", "user_id", "archived"),
        Index("ix_memories_user_namespace_active", "user_id", "namespace", "archived"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="fact")
    # 'fact' | 'preference' | 'project' | 'skill' | 'procedure' | 'other'
    namespace: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    # study/{statistics,databases,java,cpp} | projects/{jarvis,...}
    # tasks/{homework,deadlines} | personal/preferences | procedures | general
    importance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    summary: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    # 'manual' (/remember) | 'auto' (post-turn classifier) | 'agent_tool' (agent self-call)
    source_turn_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dimension),
        nullable=True,
    )
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class Approval(Base):
    """An external-write action awaiting the user's explicit OK.

    The agent never performs an external write directly. It records an Approval
    row (status='pending'), the user is shown a Telegram card with inline
    buttons, and the action only runs once status='approved'. This breaks the
    Lethal Trifecta for agent-initiated writes (see V2 §2.B, decision D-007/D-012).
    """

    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # JSON-serialized tool arguments (the payload to execute on approval).
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    # Human-readable preview shown in the approval card.
    preview: Mapped[str] = mapped_column(Text, nullable=False)
    # pending | approved | rejected | expired
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # Stable hash of (user_id, tool_name, payload) for idempotent dedup.
    request_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    decided_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ===========================================================================
# Life-OS domains (Phase 4) — personal data beyond university.
# All additive; each carries user_id (single-user today, future-proof).
# ===========================================================================


class Person(Base):
    """Personal CRM: people in Богдан's life (brother, students, friends, contacts)."""

    __tablename__ = "people"
    __table_args__ = (
        Index("ix_people_user_archived", "user_id", "archived"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    aliases: Mapped[list | None] = mapped_column(JSON, nullable=True)  # alt names/nicknames
    relation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # brother | student | friend | professor | contact | family | other
    birthday: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    interactions: Mapped[list[Interaction]] = relationship(
        back_populates="person",
        cascade="all, delete-orphan",
    )


class Interaction(Base):
    """A logged contact with a person (for follow-up nudges + history)."""

    __tablename__ = "interactions"
    __table_args__ = (
        Index("ix_interactions_person_time", "person_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # in_person | telegram | email | phone | other
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    person: Mapped[Person] = relationship(back_populates="interactions")


class Transaction(Base):
    """Money: income (e.g. tutoring lessons) and expenses (e.g. receipts)."""

    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_user_time", "user_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # income | expense
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="BGN")
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # tutoring | food | transport | subscription | shopping | other
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id", ondelete="SET NULL"), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    # manual | receipt | tutoring_auto
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Capture(Base):
    """Universal capture inbox: a thought/link/voice/photo/idea to file or recall later."""

    __tablename__ = "captures"
    __table_args__ = (
        Index("ix_captures_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="note")
    # note | link | voice | photo | idea
    content: Mapped[str | None] = mapped_column(Text, nullable=True)  # text / transcript
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="inbox")
    # inbox | filed | archived
    routed_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # e.g. task:123 | event | note | memory
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Monitor(Base):
    """A persistent watcher: ping the user when a webpage/price changes."""

    __tablename__ = "monitors"
    __table_args__ = (
        Index("ix_monitors_user_active", "user_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="webpage")
    # webpage | price
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    # keyword to watch, CSS hint, or "price<100"
    last_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Habit(Base):
    """A tracked habit (gym, water, sleep, study streak)."""

    __tablename__ = "habits"
    __table_args__ = (
        Index("ix_habits_user_active", "user_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    schedule: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # daily | weekdays | mon,wed,fri
    target: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "8 glasses"
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    logs: Mapped[list[HabitLog]] = relationship(
        back_populates="habit",
        cascade="all, delete-orphan",
    )


class HabitLog(Base):
    """One day's completion of a habit."""

    __tablename__ = "habit_logs"
    __table_args__ = (
        UniqueConstraint("habit_id", "log_date", name="uq_habit_log_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    habit_id: Mapped[int] = mapped_column(
        ForeignKey("habits.id", ondelete="CASCADE"), nullable=False
    )
    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "done" | "6"
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    habit: Mapped[Habit] = relationship(back_populates="logs")


class ConversationTurn(Base):
    """One user→assistant exchange. `pydantic_messages_*` are Pydantic AI ModelMessage blobs."""

    __tablename__ = "conversation_turns"
    __table_args__ = (
        Index("ix_conv_turns_thread_created", "thread_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)  # telegram | moodle_self | cli
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    output_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Full serialized ModelMessage[] from this turn (audit/replay)
    pydantic_messages_json: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Same data but with tool returns truncated — used for `message_history` loading
    pydantic_messages_compact: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    usage_tokens_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_tokens_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    thread: Mapped[ConversationThread] = relationship(back_populates="turns")
