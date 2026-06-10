"""Habit tracking — add habits, log daily completions, report streaks/status.

Framework-agnostic data module (NO pydantic_ai / litellm imports). The agent
layer wires these into tools; this file only talks to the DB.

Public API:
    add_habit(user_id, name, *, schedule=None, target=None) -> int
        Idempotent on (user_id, name) for an active habit: returns the existing
        active habit's id if one matches, else inserts a new habit.
    find_habit(user_id, query) -> dict | None
        Case-insensitive substring match on an active habit's name.
    log_habit(user_id, habit_query, *, value="done", log_date=None, note=None)
        -> dict   UPSERT one log per (habit_id, log_date). Auto-creates the
        habit if no active match is found. log_date is ISO 'YYYY-MM-DD' or None
        → today (now_local().date()).
    list_habits(user_id) -> list[dict]
        Active habits as [{id, name, schedule, target}].
    status(user_id) -> list[dict]
        Per active habit: {name, done_today, streak, last7}. See `status` for
        the precise streak/last7 definitions.

Streak definition:
    `streak` is the count of consecutive calendar days (local tz) that have a
    HabitLog, ending today. If today is NOT logged, the streak is measured up to
    yesterday (so a streak survives until a day is actually missed). A gap of one
    fully-unlogged day breaks the streak.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from bogi.db import get_session
from bogi.models import Habit, HabitLog
from bogi.tz import now_local


def _parse_log_date(log_date: str | None) -> date:
    """Resolve an optional ISO date string to a `date`; None → today (local)."""
    if log_date is None:
        return now_local().date()
    if isinstance(log_date, date):
        return log_date
    return date.fromisoformat(log_date)


async def add_habit(
    user_id: int,
    name: str,
    *,
    schedule: str | None = None,
    target: str | None = None,
) -> int:
    """Create a habit, or return the id of an existing active one with same name.

    Match is exact (case-insensitive) on the trimmed name among active habits.
    """
    name = name.strip()
    async with get_session() as session:
        existing = await session.execute(
            select(Habit).where(
                Habit.user_id == user_id,
                Habit.active.is_(True),
                func.lower(Habit.name) == name.lower(),
            )
        )
        row = existing.scalars().first()
        if row is not None:
            return row.id

        habit = Habit(
            user_id=user_id,
            name=name,
            schedule=schedule,
            target=target,
            active=True,
        )
        session.add(habit)
        await session.flush()
        return habit.id


async def find_habit(user_id: int, query: str) -> dict | None:
    """Case-insensitive substring match on an active habit name.

    Returns {id, name, schedule, target} for the first (lowest-id) match.
    """
    query = query.strip().lower()
    async with get_session() as session:
        result = await session.execute(
            select(Habit)
            .where(
                Habit.user_id == user_id,
                Habit.active.is_(True),
                func.lower(Habit.name).contains(query),
            )
            .order_by(Habit.id)
        )
        habit = result.scalars().first()
        if habit is None:
            return None
        return {
            "id": habit.id,
            "name": habit.name,
            "schedule": habit.schedule,
            "target": habit.target,
        }


async def log_habit(
    user_id: int,
    habit_query: str,
    *,
    value: str = "done",
    log_date: str | None = None,
    note: str | None = None,
) -> dict:
    """UPSERT a single HabitLog for (habit, day). Auto-creates the habit.

    Resolves `habit_query` to an active habit by substring; if none matches,
    a new habit is created with the query (trimmed) as its name. Per
    (habit_id, log_date) there is at most one row: an existing row for that day
    is updated (value + note overwritten — note only when provided), otherwise a
    new row is inserted.

    Returns {habit_id, habit_name, log_date, value} (log_date as ISO string).
    """
    day = _parse_log_date(log_date)
    query = habit_query.strip()

    async with get_session() as session:
        habit_row = await session.execute(
            select(Habit)
            .where(
                Habit.user_id == user_id,
                Habit.active.is_(True),
                func.lower(Habit.name).contains(query.lower()),
            )
            .order_by(Habit.id)
        )
        habit = habit_row.scalars().first()

        if habit is None:
            habit = Habit(user_id=user_id, name=query, active=True)
            session.add(habit)
            await session.flush()

        log_row = await session.execute(
            select(HabitLog).where(
                HabitLog.habit_id == habit.id,
                HabitLog.log_date == day,
            )
        )
        log = log_row.scalars().first()

        if log is None:
            log = HabitLog(
                habit_id=habit.id,
                log_date=day,
                value=value,
                note=note,
            )
            session.add(log)
        else:
            log.value = value
            if note is not None:
                log.note = note

        await session.flush()

        return {
            "habit_id": habit.id,
            "habit_name": habit.name,
            "log_date": day.isoformat(),
            "value": value,
        }


async def list_habits(user_id: int) -> list[dict]:
    """Active habits for this user as [{id, name, schedule, target}]."""
    async with get_session() as session:
        result = await session.execute(
            select(Habit)
            .where(Habit.user_id == user_id, Habit.active.is_(True))
            .order_by(Habit.id)
        )
        return [
            {
                "id": h.id,
                "name": h.name,
                "schedule": h.schedule,
                "target": h.target,
            }
            for h in result.scalars().all()
        ]


async def status(user_id: int) -> list[dict]:
    """Per active habit: {name, done_today, streak, last7}.

    - done_today: a log exists for today (local date).
    - streak: consecutive logged days ending today; if today is unlogged, the
      run is measured up to yesterday (see module docstring).
    - last7: the 7 dates ending today, oldest→newest, each {date, done}.
    """
    today = now_local().date()
    async with get_session() as session:
        habits_result = await session.execute(
            select(Habit)
            .where(Habit.user_id == user_id, Habit.active.is_(True))
            .order_by(Habit.id)
        )
        habits = habits_result.scalars().all()
        if not habits:
            return []

        habit_ids = [h.id for h in habits]
        logs_result = await session.execute(
            select(HabitLog.habit_id, HabitLog.log_date).where(
                HabitLog.habit_id.in_(habit_ids)
            )
        )
        logged: dict[int, set[date]] = {hid: set() for hid in habit_ids}
        for habit_id, log_date in logs_result.all():
            logged[habit_id].add(log_date)

    out: list[dict] = []
    for habit in habits:
        days = logged[habit.id]
        done_today = today in days

        # last7: oldest → newest, ending today.
        last7 = [
            {
                "date": (today - timedelta(days=offset)).isoformat(),
                "done": (today - timedelta(days=offset)) in days,
            }
            for offset in range(6, -1, -1)
        ]

        # streak: count back from today (or yesterday if today unlogged).
        streak = 0
        cursor = today if done_today else today - timedelta(days=1)
        while cursor in days:
            streak += 1
            cursor -= timedelta(days=1)

        out.append(
            {
                "name": habit.name,
                "done_today": done_today,
                "streak": streak,
                "last7": last7,
            }
        )
    return out
