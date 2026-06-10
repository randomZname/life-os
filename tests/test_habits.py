"""Habit-tracking tests (DB-touching).

Coverage:
- add_habit: idempotent on a duplicate active name (same id returned).
- log_habit: auto-creates the habit when no match; UPSERTs per day (logging the
  same day twice updates the single row instead of inserting a second).
- status: streak == 2 when today and yesterday are logged; done_today True after
  logging today; last7 always length 7.

Each test uses a sentinel (BIG random) user_id and hard-deletes that user's
habits in a finally block — habit_logs cascade via the FK ondelete.
"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest

from bogi.modules import habits
from bogi.tz import now_local


def _fake_user() -> int:
    return 9_100_000_000 + random.randint(1, 10_000_000)


async def _cleanup(user_id: int) -> None:
    from sqlalchemy import delete

    from bogi.db import get_session
    from bogi.models import Habit

    async with get_session() as session:
        await session.execute(delete(Habit).where(Habit.user_id == user_id))


@pytest.mark.asyncio
async def test_add_habit_idempotent_on_duplicate_name():
    uid = _fake_user()
    try:
        first = await habits.add_habit(uid, "Фитнес", schedule="daily")
        second = await habits.add_habit(uid, "фитнес")  # case-insensitive dup
        assert first == second

        items = await habits.list_habits(uid)
        assert len(items) == 1
        assert items[0]["id"] == first
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_log_habit_autocreates_and_upserts():
    uid = _fake_user()
    try:
        # No habit yet → log_habit auto-creates it.
        res1 = await habits.log_habit(uid, "Вода", value="3")
        assert res1["habit_name"] == "Вода"
        habit_id = res1["habit_id"]

        items = await habits.list_habits(uid)
        assert len(items) == 1

        # Logging the same day again UPDATES the single row (no second insert).
        res2 = await habits.log_habit(uid, "Вода", value="8", note="осем чаши")
        assert res2["habit_id"] == habit_id
        assert res2["value"] == "8"

        # Verify exactly one log row exists for that habit/day.
        from sqlalchemy import func, select

        from bogi.db import get_session
        from bogi.models import HabitLog

        async with get_session() as session:
            count = await session.execute(
                select(func.count())
                .select_from(HabitLog)
                .where(HabitLog.habit_id == habit_id)
            )
            assert count.scalar_one() == 1
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_status_streak_two_and_done_today():
    uid = _fake_user()
    try:
        today = now_local().date()
        yesterday = today - timedelta(days=1)

        await habits.log_habit(uid, "Четене", log_date=yesterday.isoformat())
        await habits.log_habit(uid, "Четене", log_date=today.isoformat())

        rows = await habits.status(uid)
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "Четене"
        assert row["done_today"] is True
        assert row["streak"] == 2
        assert len(row["last7"]) == 7

        # last7 oldest→newest; only today and yesterday are done.
        done_dates = {d["date"] for d in row["last7"] if d["done"]}
        assert done_dates == {today.isoformat(), yesterday.isoformat()}
        assert row["last7"][-1]["date"] == today.isoformat()
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_find_habit_substring_and_miss():
    uid = _fake_user()
    try:
        await habits.add_habit(uid, "Сутрешна разходка")
        found = await habits.find_habit(uid, "разходка")
        assert found is not None
        assert found["name"] == "Сутрешна разходка"

        assert await habits.find_habit(uid, "несъществуващ") is None
    finally:
        await _cleanup(uid)
