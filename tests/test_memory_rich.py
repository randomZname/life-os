"""Memory v2 (rich recall) tests.

Coverage for the three M2 features layered on top of v1:
- usage bump: retrieve_relevant increments access_count + stamps
  last_accessed_at on the returned rows (best-effort, never fatal).
- usage-aware ranking: usage_factor is a 0..1 monotonic saturating function;
  a more-recalled memory ranks >= a less-recalled near-identical peer.
- hybrid retrieval: a distinctive keyword surfaces a memory via the pg_trgm
  keyword path even when it is not the closest vector neighbour.
- composite weights sum to 1.0 (the documented invariant).

DB-touching tests use a unique synthetic user_id and hard-delete their rows
afterwards so production memories (and concurrent CI runs) stay clean.
"""

from __future__ import annotations

import random

import pytest

from bogi.modules import long_term_memory as ltm

# -------- pure-function tests (no DB) ----------------------------------------

def test_weights_sum_to_one():
    total = ltm.W_COSINE + ltm.W_IMPORTANCE + ltm.W_RECENCY + ltm.W_USAGE
    assert total == pytest.approx(1.0), f"composite weights must sum to 1.0, got {total}"


def test_usage_factor_monotonic_and_bounded():
    # 0 → 0, strictly increasing, asymptotically approaching (but < ) 1.0.
    assert ltm.usage_factor(0) == pytest.approx(0.0)
    assert ltm.usage_factor(None) == pytest.approx(0.0)
    prev = -1.0
    for n in range(0, 50):
        v = ltm.usage_factor(n)
        assert 0.0 <= v < 1.0
        assert v >= prev, "usage_factor must be monotonically non-decreasing"
        prev = v
    # frequently recalled clearly beats never recalled
    assert ltm.usage_factor(20) > ltm.usage_factor(1)


# -------- DB-touching tests --------------------------------------------------

def _fake_user() -> int:
    return 9_100_000_000 + random.randint(1, 10_000_000)


async def _cleanup(user_id: int) -> None:
    """Hard-delete all rows for this synthetic user."""
    from sqlalchemy import delete

    from bogi.db import get_session
    from bogi.models import Memory

    async with get_session() as session:
        await session.execute(delete(Memory).where(Memory.user_id == user_id))


async def _read_row(user_id: int, mem_id: int):
    from sqlalchemy import select

    from bogi.db import get_session
    from bogi.models import Memory

    async with get_session() as session:
        stmt = select(Memory).where(Memory.id == mem_id).where(Memory.user_id == user_id)
        return (await session.execute(stmt)).scalar_one()


@pytest.mark.asyncio
async def test_usage_bump_on_retrieval():
    uid = _fake_user()
    try:
        mem_id, _ = await ltm.save_or_update(
            uid,
            "Богдан учи бази данни и нормални форми за изпита.",
            namespace="study/databases",
            kind="fact",
        )
        out = await ltm.retrieve_relevant(uid, "бази данни нормални форми", limit=5)
        ids = [r["id"] for r in out]
        assert mem_id in ids, f"saved memory should be retrieved; got {ids}"

        row = await _read_row(uid, mem_id)
        assert row.access_count >= 1, "retrieval must bump access_count"
        assert row.last_accessed_at is not None, "retrieval must stamp last_accessed_at"
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_usage_affects_score():
    uid = _fake_user()
    try:
        hot_id, _ = await ltm.save_or_update(
            uid,
            "Python tip: use list comprehensions for clarity (alpha).",
            namespace="general",
            kind="fact",
            importance_score=0.5,
        )
        cold_id, _ = await ltm.save_or_update(
            uid,
            "Python tip: use generator expressions for memory (beta).",
            namespace="general",
            kind="fact",
            importance_score=0.5,
        )
        # Recall the "hot" memory several times so its access_count climbs.
        for _ in range(6):
            await ltm.retrieve_relevant(uid, "list comprehensions alpha", limit=1)

        hot = await _read_row(uid, hot_id)
        cold = await _read_row(uid, cold_id)
        assert hot.access_count > cold.access_count

        # usage_factor reflects that ordering.
        assert ltm.usage_factor(hot.access_count) >= ltm.usage_factor(cold.access_count)

        # On a neutral query both are candidates; the more-used one ranks >=.
        ranked = await ltm.retrieve_relevant(uid, "Python tip", limit=5)
        pos = {r["id"]: i for i, r in enumerate(ranked)}
        assert hot_id in pos and cold_id in pos, f"both expected in {list(pos)}"
        assert pos[hot_id] <= pos[cold_id], "more-recalled memory should rank >= peer"
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_hybrid_keyword_retrieval():
    uid = _fake_user()
    try:
        kw_id, _ = await ltm.save_or_update(
            uid,
            "Проектът се казва Zxqwobble и ползва специален конфиг файл.",
            namespace="projects/jarvis",
            kind="project",
            importance_score=0.5,
        )
        # Decoys that are semantically unrelated to the distinctive keyword.
        for i in range(6):
            await ltm.save_or_update(
                uid,
                f"Случайна бележка номер {i} за нещо съвсем различно.",
                namespace="general",
                kind="fact",
                importance_score=0.5,
            )

        out = await ltm.retrieve_relevant(uid, "Zxqwobble", limit=5)
        ids = [r["id"] for r in out]
        assert kw_id in ids, f"keyword path should surface the distinctive memory; got {ids}"
    finally:
        await _cleanup(uid)
