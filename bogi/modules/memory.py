"""Conversation memory store.

One active thread per user, shared across Telegram + Moodle channels.
Each turn persists the full Pydantic AI ModelMessage[] AND a compacted copy
(tool returns truncated) for sliding-window replay.

Public API:
    get_or_create_thread(user_id) -> int
    new_thread(user_id) -> int                  # archives current, creates fresh
    load_history(thread_id, n=20) -> list[ModelMessage]
    save_turn(thread_id, channel, input_text, output_text, new_messages, usage)
    user_lock(user_id) -> asyncio.Lock          # per-user serialization
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ToolReturnPart,
)
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from bogi.db import get_session
from bogi.models import ConversationThread, ConversationTurn

logger = logging.getLogger(__name__)

# Sliding window: how many recent turns to feed back as message_history
DEFAULT_HISTORY_TURNS = 10

# When (turns_after_summary >= SUMMARIZE_TRIGGER) → run rolling summary
SUMMARIZE_TRIGGER = 15

# Older N turns get folded into the summary; we keep KEEP_RECENT_AFTER_SUMMARY
# unsummarized so the LLM still has fine-grained context for follow-ups.
KEEP_RECENT_AFTER_SUMMARY = 5

# Tool returns longer than this get replaced with a stub in the compact blob
TOOL_RETURN_TRUNCATE_CHARS = 2000

# Per-user asyncio locks so concurrent Telegram + Moodle monitor calls serialize
_locks: dict[int, asyncio.Lock] = {}


def user_lock(user_id: int) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    return lock


async def get_or_create_thread(user_id: int) -> int:
    """Return active (non-archived) thread id; create one if absent.

    The DB enforces at most one active thread per user via a partial UNIQUE
    index (`ux_conv_threads_one_active`). If a concurrent caller wins the
    insert race we retry the lookup.
    """
    for _ in range(3):
        async with get_session() as session:
            stmt = (
                select(ConversationThread.id)
                .where(ConversationThread.user_id == user_id)
                .where(ConversationThread.archived.is_(False))
                .order_by(ConversationThread.id.desc())
                .limit(1)
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is not None:
                return int(row)
            thread = ConversationThread(user_id=user_id)
            session.add(thread)
            try:
                await session.flush()
                return thread.id
            except IntegrityError:
                # Lost the race — another caller inserted first; rollback + retry SELECT.
                await session.rollback()
                continue
    raise RuntimeError(f"get_or_create_thread: gave up after 3 retries for user {user_id}")


async def new_thread(user_id: int) -> int:
    """Archive any active thread and open a fresh one."""
    async with get_session() as session:
        await session.execute(
            update(ConversationThread)
            .where(ConversationThread.user_id == user_id)
            .where(ConversationThread.archived.is_(False))
            .values(archived=True)
        )
        thread = ConversationThread(user_id=user_id)
        session.add(thread)
        await session.flush()
        return thread.id


def _compact_messages(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Return a deep copy of messages with oversized tool returns truncated."""
    # Round-trip through the type adapter to clone — Pydantic models are not deep-copyable safely.
    blob = ModelMessagesTypeAdapter.dump_json(messages)
    clone: list[ModelMessage] = ModelMessagesTypeAdapter.validate_json(blob)

    for msg in clone:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            content_str = part.content if isinstance(part.content, str) else str(part.content)
            if len(content_str) > TOOL_RETURN_TRUNCATE_CHARS:
                truncated = (
                    content_str[:TOOL_RETURN_TRUNCATE_CHARS]
                    + f"\n<truncated original_length={len(content_str)}>"
                )
                part.content = truncated
    return clone


async def load_history(thread_id: int, n: int = DEFAULT_HISTORY_TURNS) -> list[ModelMessage]:
    """Load the last `n` turns AFTER the summarized portion (chronological)."""
    async with get_session() as session:
        thread = await session.get(ConversationThread, thread_id)
        cutoff = thread.summary_until_turn_id if thread else None

        stmt = select(ConversationTurn.pydantic_messages_compact).where(
            ConversationTurn.thread_id == thread_id
        )
        if cutoff is not None:
            stmt = stmt.where(ConversationTurn.id > cutoff)
        stmt = stmt.order_by(ConversationTurn.id.desc()).limit(n)
        rows = (await session.execute(stmt)).scalars().all()

    messages: list[ModelMessage] = []
    for blob in reversed(rows):
        try:
            messages.extend(ModelMessagesTypeAdapter.validate_json(blob))
        except Exception:
            logger.exception("Failed to decode message blob in thread %s — skipping", thread_id)
    return messages


async def load_thread_summary(thread_id: int) -> str | None:
    """Current rolling summary of older turns, or None if none yet."""
    async with get_session() as session:
        thread = await session.get(ConversationThread, thread_id)
        return thread.summary if thread else None


async def _count_unsummarized_turns(thread_id: int) -> tuple[int, int | None]:
    """Returns (count_of_turns_after_summary, summary_until_turn_id)."""
    from sqlalchemy import func
    async with get_session() as session:
        thread = await session.get(ConversationThread, thread_id)
        cutoff = thread.summary_until_turn_id if thread else None
        stmt = select(func.count(ConversationTurn.id)).where(
            ConversationTurn.thread_id == thread_id
        )
        if cutoff is not None:
            stmt = stmt.where(ConversationTurn.id > cutoff)
        count = (await session.execute(stmt)).scalar_one()
        return int(count), cutoff


async def maybe_summarize(thread_id: int) -> None:
    """If the thread has accumulated too many unsummarized turns, fold older ones into a summary.

    Runs a single direct LLM call against LiteLLM. Idempotent — safe to invoke after every save.
    """
    count, cutoff = await _count_unsummarized_turns(thread_id)
    if count < SUMMARIZE_TRIGGER:
        return

    # Fetch all unsummarized turns, fold all but the last KEEP_RECENT_AFTER_SUMMARY
    fold_count = count - KEEP_RECENT_AFTER_SUMMARY
    if fold_count <= 0:
        return

    async with get_session() as session:
        stmt = select(ConversationTurn).where(ConversationTurn.thread_id == thread_id)
        if cutoff is not None:
            stmt = stmt.where(ConversationTurn.id > cutoff)
        stmt = stmt.order_by(ConversationTurn.id.asc())
        turns = (await session.execute(stmt)).scalars().all()

        thread = await session.get(ConversationThread, thread_id)
        previous_summary = thread.summary if thread else None

    to_fold = turns[:fold_count]
    last_folded_id = to_fold[-1].id

    transcript_lines: list[str] = []
    for t in to_fold:
        transcript_lines.append(f"[{t.channel}] потребител: {t.input_text}")
        # Keep assistant output short in transcript to bound prompt size
        out = t.output_text or ""
        if len(out) > 800:
            out = out[:800] + " …(съкратено)"
        transcript_lines.append(f"асистент: {out}")
    transcript = "\n\n".join(transcript_lines)

    prev_block = f"\n\nПредишно резюме (актуализирай го):\n{previous_summary}\n" if previous_summary else ""
    sys_prompt = (
        "Ти си компресор на разговори. Получаваш транскрипт между Богдан и неговия AI асистент. "
        "Напиши кратко, конкретно резюме (300-500 думи на български). "
        "Фокусирай се на: важни факти за Богдан, текущи задачи и проекти, решения, "
        "обещания, имена/файлове/кодови артефакти. Без баналности. "
        "Изхвърли small-talk."
    )
    user_prompt = f"{prev_block}\nНов транскрипт за резюмиране:\n\n{transcript}"

    summary_text = await _llm_summarize(sys_prompt, user_prompt)
    if not summary_text:
        logger.warning("Summarize: LLM returned empty result, thread %s", thread_id)
        return

    async with get_session() as session:
        await session.execute(
            update(ConversationThread)
            .where(ConversationThread.id == thread_id)
            .values(summary=summary_text, summary_until_turn_id=last_folded_id)
        )
    logger.info(
        "Summarized %d turns into thread %s (cutoff=%s, chars=%d)",
        fold_count, thread_id, last_folded_id, len(summary_text),
    )


async def _llm_summarize(system_text: str, user_text: str) -> str:
    """Direct LiteLLM call. Bypasses the agent to avoid recursion + tool noise."""
    import httpx
    from bogi.config import settings

    url = f"{settings.litellm_base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": "cheap",
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": 1500,
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {settings.litellm_master_key}"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        logger.exception("Summarize LLM call failed")
        return ""


async def save_turn(
    thread_id: int,
    channel: str,
    input_text: str,
    output_text: str,
    new_messages: list[ModelMessage],
    usage: Any = None,
) -> int:
    """Persist a single user→assistant exchange."""
    full_blob = bytes(ModelMessagesTypeAdapter.dump_json(new_messages))
    compact_blob = bytes(ModelMessagesTypeAdapter.dump_json(_compact_messages(new_messages)))

    tokens_in = getattr(usage, "request_tokens", None) if usage else None
    tokens_out = getattr(usage, "response_tokens", None) if usage else None

    async with get_session() as session:
        turn = ConversationTurn(
            thread_id=thread_id,
            channel=channel,
            input_text=input_text,
            output_text=output_text,
            pydantic_messages_json=full_blob,
            pydantic_messages_compact=compact_blob,
            usage_tokens_input=tokens_in,
            usage_tokens_output=tokens_out,
        )
        session.add(turn)
        # bump thread.updated_at — `turn.created_at` isn't populated until flush,
        # so use an explicit timestamp to avoid writing None.
        await session.execute(
            update(ConversationThread)
            .where(ConversationThread.id == thread_id)
            .values(updated_at=datetime.utcnow())
        )
        await session.flush()
        return turn.id
