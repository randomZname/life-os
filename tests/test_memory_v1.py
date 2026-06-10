"""Memory v1 tests.

Coverage:
- should_save_memory: rejects secrets, smalltalk, empty/too-short/too-long;
  accepts useful sentences.
- _infer_namespace_hint: matches study/projects/preferences/procedures rules;
  returns None on no match.
- save_or_update: creates when no neighbor; updates when one exists in same ns;
  treats different namespace as distinct even if content is identical.
- retrieve_relevant: honors limit; ranks namespace_hint matches above peers.

DB-touching tests use a sentinel user_id (BIG random) and archive their
fixtures after each test so they don't pollute production memories.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from bogi.modules import long_term_memory as ltm
from bogi.agent import _infer_namespace_hint


# -------- should_save_memory (pure function, no DB) --------------------------

@pytest.mark.parametrize("text,expected", [
    ("", False),
    ("ok", False),
    ("благодаря", False),
    ("Hi", False),
    ("a" * 5, False),  # too short
    ("a" * 1600, False),  # too long
    ("Anthropic key: sk-ant-api03-aabbccddeeff112233445566", False),
    ("Use OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwx12345", False),
    ("My Google secret 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", False),
    ("github PAT ghp_abcdefghijklmnopqrstuvwxyz0123456789", False),
    ("password=hunter2letmein", False),
    ("Token = eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxw"
     "RJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c", False),
    ("Богдан предпочита snake_case в Python над camelCase",  True),
    ("Имам контролно по бази данни на 30.05.2026 в зала 200", True),
])
def test_should_save_memory(text, expected):
    allow, _why = ltm.should_save_memory(text)
    assert allow is expected, f"text={text!r} expected save={expected}"


# -------- namespace hint inference (pure function) ---------------------------

@pytest.mark.parametrize("query,expected", [
    ("обясни ми нормалното разпределение", "study/statistics"),
    ("какво е t-test", "study/statistics"),
    ("какво е foreign key в SQL", "study/databases"),
    ("бази данни нормални форми", "study/databases"),
    ("как работи std::unique_ptr в C++", "study/cpp"),
    ("Spring Boot в Java", "study/java"),
    ("оправи watchdog-а в jarvis", "projects/jarvis"),
    ("предпочитам tabs над spaces", "personal/preferences"),
    ("как се настройва Docker за нов проект", "procedures"),
    ("имам домашно за петък", "tasks/homework"),
    ("какво време е навън", None),
])
def test_infer_namespace_hint(query, expected):
    assert _infer_namespace_hint(query) == expected


# -------- DB-touching tests --------------------------------------------------

# Each test uses a unique pseudo-user so concurrent CI runs don't collide
# and the production user (1114488869) stays clean.
def _fake_user() -> int:
    return 9_000_000_000 + random.randint(1, 10_000_000)


async def _cleanup(user_id: int) -> None:
    """Hard-delete all rows for this synthetic user."""
    from sqlalchemy import delete
    from bogi.db import get_session
    from bogi.models import Memory
    async with get_session() as session:
        await session.execute(delete(Memory).where(Memory.user_id == user_id))


@pytest.mark.asyncio
async def test_save_or_update_creates_new():
    uid = _fake_user()
    try:
        mem_id, action = await ltm.save_or_update(
            uid,
            "Богдан предпочита snake_case в Python.",
            namespace="personal/preferences",
            kind="preference",
            importance_score=0.8,
        )
        assert action == "created"
        assert isinstance(mem_id, int) and mem_id > 0
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_save_or_update_merges_near_duplicate_same_namespace():
    uid = _fake_user()
    try:
        first_id, first_action = await ltm.save_or_update(
            uid,
            "Богдан предпочита snake_case в Python над camelCase.",
            namespace="personal/preferences",
            kind="preference",
            importance_score=0.6,
        )
        assert first_action == "created"

        second_id, second_action = await ltm.save_or_update(
            uid,
            "Богдан предпочита snake_case в Python.",  # same fact, shorter
            namespace="personal/preferences",
            kind="preference",
            importance_score=0.9,
        )
        assert second_action == "updated"
        assert second_id == first_id, "near-duplicate should update, not create"

        items = await ltm.list_memories(uid, namespace="personal/preferences")
        assert len(items) == 1
        # max-importance promotion
        assert items[0]["importance"] >= 0.9
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_different_namespace_is_distinct_even_if_content_similar():
    uid = _fake_user()
    try:
        a_id, _ = await ltm.save_or_update(
            uid,
            "Имам контролно по статистика на 30.05.2026",
            namespace="study/statistics",
            kind="fact",
        )
        b_id, b_action = await ltm.save_or_update(
            uid,
            "Имам контролно по статистика на 30.05.2026",
            namespace="tasks/deadlines",
            kind="fact",
        )
        assert b_action == "created"
        assert a_id != b_id
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_retrieve_respects_limit():
    uid = _fake_user()
    try:
        for i in range(8):
            await ltm.save_or_update(
                uid, f"Memory entry number {i} about databases.",
                namespace="study/databases", kind="fact",
            )
        out = await ltm.retrieve_relevant(uid, "databases", limit=3)
        assert len(out) <= 3
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_retrieve_namespace_hint_boosts_match():
    uid = _fake_user()
    try:
        prefer_id, _ = await ltm.save_or_update(
            uid,
            "Богдан предпочита snake_case в Python.",
            namespace="personal/preferences",
            kind="preference",
            importance_score=0.7,
        )
        # Add several decoys in another ns that match the query keyword too.
        for i in range(5):
            await ltm.save_or_update(
                uid,
                f"Python tip number {i}: use list comprehensions wisely.",
                namespace="general",
                kind="fact",
                importance_score=0.5,
            )

        # With matching hint, the preferences memory should be in top results.
        ranked = await ltm.retrieve_relevant(
            uid, "Python style", namespace_hint="personal/preferences", limit=3,
        )
        ids = [r["id"] for r in ranked]
        assert prefer_id in ids, f"preferences memory missing with hint; got {ids}"
    finally:
        await _cleanup(uid)
