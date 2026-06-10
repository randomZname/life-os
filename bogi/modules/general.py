"""General-purpose tools: tasks and date helpers."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select

from bogi.db import get_session
from bogi.models import Task
from bogi.tz import now_local


async def task_create(title: str, due_date: str | None = None, notes: str | None = None) -> int:
    """Create a new task. due_date is ISO date string YYYY-MM-DD."""
    parsed_due = date.fromisoformat(due_date) if due_date else None
    async with get_session() as session:
        task = Task(title=title, due_date=parsed_due, notes=notes)
        session.add(task)
        await session.flush()
        return task.id


async def task_list(status: str = "open") -> list[dict[str, Any]]:
    """List tasks filtered by status (open|done|cancelled|all)."""
    async with get_session() as session:
        stmt = select(Task)
        if status != "all":
            stmt = stmt.where(Task.status == status)
        stmt = stmt.order_by(Task.due_date.nulls_last(), Task.created_at)
        result = await session.execute(stmt)
        tasks = result.scalars().all()
        return [
            {
                "id": t.id,
                "title": t.title,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "status": t.status,
                "notes": t.notes,
            }
            for t in tasks
        ]


async def task_complete(task_id: int) -> bool:
    """Mark a task as done."""
    async with get_session() as session:
        task = await session.get(Task, task_id)
        if not task:
            return False
        task.status = "done"
        return True


def get_today_info() -> dict[str, str]:
    """Current date, weekday and LOCAL time (Europe/Sofia, DST-correct)."""
    now = now_local()
    days_bg = ["понеделник", "вторник", "сряда", "четвъртък", "петък", "събота", "неделя"]
    return {
        "date": now.date().isoformat(),
        "day_of_week_bg": days_bg[now.weekday()],
        "day_of_week_en": now.strftime("%A"),
        "time": now.strftime("%H:%M"),
        # Local wall-clock, NO timezone suffix on purpose — exposing an offset
        # made the model convert clock times and shift events by 2-3h.
        "iso_datetime": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "Това е българско време. Часове записвай дословно — не смятай UTC, без Z/offset.",
    }
