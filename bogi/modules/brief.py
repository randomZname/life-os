"""Deterministic morning brief — днешен дневен ред + предстоящи Moodle крайни срокове.

Framework-agnostic. NO pydantic_ai / litellm. NO LLM call. Чисто събиране на данни
от LIGHT пътищата (``calendars.agenda`` + ``fmi.get_upcoming_events``) и детерминистично
форматиране на български. Всеки източник е обвит в свой try/except — ``compose_brief``
НИКОГА не хвърля към извикващия, а деградира до частичен бриф.
"""

from __future__ import annotations

import logging

from bogi.modules import calendars, capture, habits, money, people

logger = logging.getLogger(__name__)

# --- Display labels -----------------------------------------------------------
# Agenda event_class ∈ {lesson, university, personal, other}.
_AGENDA_SECTIONS: list[tuple[str, str]] = [
    ("lesson", "📚 Уроци"),
    ("university", "🎓 Университет"),
    ("personal", "🎉 Лично"),
    ("other", "🔔 Друго"),
]
# Moodle deadline kind ∈ {assignment, quiz, other}.
_DEADLINE_SECTIONS: list[tuple[str, str]] = [
    ("assignment", "📝 ДОМАШНИ:"),
    ("quiz", "📋 ТЕСТОВЕ / КОНТРОЛНИ:"),
    ("other", "🔔 ДРУГО:"),
]

_EMPTY_LINE = "Днес нямаш нищо записано. 🎉"


def _event_time(start: str) -> str:
    """Extract a HH:MM time from a gcal `start` value.

    `start` is either an ISO datetime ('2026-06-03T10:00:00+03:00' / no offset)
    or a bare date ('2026-06-03') for all-day events. Returns '' for all-day.
    """
    s = (start or "").strip()
    if not s or "T" not in s:
        return ""  # bare date → all-day, no clock time
    clock = s.split("T", 1)[1]
    # Strip timezone suffix and seconds: '10:00:00+03:00' → '10:00'.
    clock = clock.split("+")[0].split("Z")[0]
    parts = clock.split(":")
    if len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    return ""


def _agenda_line(ev: dict) -> str:
    """One formatted bullet for an agenda event (time + summary + context)."""
    summary = (ev.get("summary") or "").strip() or "(без заглавие)"
    time = _event_time(ev.get("start", ""))
    prefix = f"⏰ {time} " if time else ""

    suffix = ""
    if ev.get("owner") == "martin":
        suffix = " (на брат ти Мартин)"
    else:
        cal = (ev.get("calendar") or "").strip()
        # Surface the calendar name only when it adds context (work/tutoring).
        if ev.get("cal_type") == "work" and cal:
            suffix = f" [{cal}]"
    return f"• {prefix}{summary}{suffix}"


async def _build_agenda_block(today: str) -> list[str]:
    try:
        events = await calendars.agenda(date_from=today, date_to=today)
    except Exception:
        logger.exception("compose_brief: agenda source failed")
        return []

    if not events:
        return []

    by_class: dict[str, list[dict]] = {}
    for ev in events:
        by_class.setdefault(ev.get("event_class", "other"), []).append(ev)

    lines = [f"📅 Днес ({today})"]
    for key, label in _AGENDA_SECTIONS:
        group = by_class.get(key)
        if not group:
            continue
        lines.append("")
        lines.append(label)
        lines.extend(_agenda_line(ev) for ev in group)
    return lines


def _deadline_line(d: dict) -> str:
    """One formatted bullet for a Moodle deadline, mirroring SYSTEM_PROMPT format."""
    course = (d.get("course") or "").strip()
    title = (d.get("title") or "").strip() or "(без заглавие)"
    head = f"• {course} — {title}" if course else f"• {title}"
    time_text = (d.get("time_text") or "").strip()
    if time_text:
        return f"{head}\n  ⏰ {time_text}"
    return head


async def _build_deadlines_block(fmi) -> list[str]:
    """Render the „📅 Предстоящи задачи" block. Returns [] if there are none."""
    try:
        deadlines = await fmi.get_upcoming_events()
    except Exception:
        logger.exception("compose_brief: Moodle deadlines source failed")
        return []

    if not deadlines:
        return []

    by_kind: dict[str, list[dict]] = {}
    for d in deadlines:
        by_kind.setdefault(d.get("kind", "other"), []).append(d)

    lines = ["📅 Предстоящи задачи"]
    for key, label in _DEADLINE_SECTIONS:
        group = by_kind.get(key)
        if not group:
            continue
        lines.append("")
        lines.append(label)
        lines.extend(_deadline_line(d) for d in group)
    return lines


async def _build_people_block(user_id: int) -> list[str]:
    """Upcoming birthdays + people to reach out to."""
    try:
        due = await people.due_followups(user_id)
    except Exception:
        logger.exception("compose_brief: people source failed")
        return []
    bdays = due.get("birthdays") or []
    stale = (due.get("stale") or [])[:3]
    if not bdays and not stale:
        return []
    lines = ["👥 Хора"]
    for b in bdays:
        when = "днес 🎂" if b.get("in_days") == 0 else f"след {b.get('in_days')} дни"
        lines.append(f"• Рожден ден: {b.get('name')} ({when})")
    for p in stale:
        lines.append(f"• Потърси {p.get('name')} (от {p.get('days_since')} дни без контакт)")
    return lines


async def _build_money_block(user_id: int) -> list[str]:
    """This month's income/expense snapshot (only if there is activity)."""
    try:
        m = await money.monthly_summary(user_id)
    except Exception:
        logger.exception("compose_brief: money source failed")
        return []
    if not m.get("count"):
        return []
    cur = m.get("currency", "BGN")
    return [
        "💰 Този месец",
        f"• Приходи: {m.get('income_total', 0):.2f} {cur} · Разходи: "
        f"{m.get('expense_total', 0):.2f} {cur} · Нето: {m.get('net', 0):.2f} {cur}",
    ]


async def _build_habits_block(user_id: int) -> list[str]:
    """Habit streaks + what's not done yet today."""
    try:
        st = await habits.status(user_id)
    except Exception:
        logger.exception("compose_brief: habits source failed")
        return []
    if not st:
        return []
    lines = ["🏃 Навици"]
    for h in st:
        mark = "✅" if h.get("done_today") else "⬜"
        streak = h.get("streak", 0)
        tail = f" · 🔥{streak}" if streak else ""
        lines.append(f"{mark} {h.get('name')}{tail}")
    return lines


async def _build_inbox_block(user_id: int) -> list[str]:
    """Count of un-filed captures, as a gentle nudge."""
    try:
        items = await capture.inbox(user_id)
    except Exception:
        logger.exception("compose_brief: capture source failed")
        return []
    if not items:
        return []
    return [f"📥 Inbox: {len(items)} за разчистване"]


async def compose_brief(user_id: int, fmi, *, days: int = 1) -> str:
    """Deterministic morning brief: today's classified agenda + Moodle deadlines.

    `fmi` is a live FMIScraper (passed in — never spin up a second browser).

    Resilient: each data source is isolated; a failure in one still yields the
    other. Never raises — degrades to a partial (or empty-state) brief.
    """
    from bogi.tz import now_local

    today = now_local().strftime("%Y-%m-%d")

    agenda_block: list[str] = []
    try:
        agenda_block = await _build_agenda_block(today)
    except Exception:
        logger.exception("compose_brief: agenda block failed")

    deadlines_block: list[str] = []
    try:
        deadlines_block = await _build_deadlines_block(fmi)
    except Exception:
        logger.exception("compose_brief: deadlines block failed")

    # Life-OS blocks (each self-isolating; never breaks the brief).
    people_block: list[str] = []
    money_block: list[str] = []
    habits_block: list[str] = []
    inbox_block: list[str] = []
    try:
        people_block = await _build_people_block(user_id)
    except Exception:
        logger.exception("compose_brief: people block failed")
    try:
        money_block = await _build_money_block(user_id)
    except Exception:
        logger.exception("compose_brief: money block failed")
    try:
        habits_block = await _build_habits_block(user_id)
    except Exception:
        logger.exception("compose_brief: habits block failed")
    try:
        inbox_block = await _build_inbox_block(user_id)
    except Exception:
        logger.exception("compose_brief: inbox block failed")

    sections = [
        b for b in (agenda_block, deadlines_block, people_block,
                    habits_block, money_block, inbox_block) if b
    ]
    if not sections:
        return _EMPTY_LINE

    parts = ["\n".join(block) for block in sections]
    return "\n\n".join(parts)
