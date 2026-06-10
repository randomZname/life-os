"""Calendar classification + multi-calendar agenda.

Богдан свърза много календари към Google. Правилото (по негова инструкция):
гледаме САМО iOS-внесените календари (id съдържа ``import``). Всеки събитие се
маркира с:
  - ``calendar``  — име на изходния календар
  - ``owner``     — `bogdan` | `martin`  (само „Марто" е на брат му Мартин)
  - ``cal_type``  — `work` | `personal` | `university` | `other` (по име на календара)
  - ``event_class`` — `lesson` | `personal` | `other` (евристика по заглавието)

Framework-agnostic. Чете през ``bogi.modules.gcal``. Без pydantic_ai/litellm.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from bogi.modules import gcal

# --- Owner / type mapping by calendar name -----------------------------------
OWNER_BY_NAME: dict[str, str] = {
    "Марто": "martin",
}
# Само „Work" е работа (частно преподаване). Останалите iOS календари
# („Untitled", „Unnamed event") са смесени → other на ниво календар, а реалният
# вид се решава по събитие (уни / лично / разни). По инструкция на Богдан.
TYPE_BY_NAME: dict[str, str] = {
    "Work": "work",
}
DEFAULT_OWNER = "bogdan"
DEFAULT_TYPE = "other"


def _is_ios(cal: dict[str, Any]) -> bool:
    """iOS-imported calendars have 'import' in their id."""
    return "import" in (cal.get("id") or "").lower()


def owner_of(name: str) -> str:
    return OWNER_BY_NAME.get(name, DEFAULT_OWNER)


def type_of(name: str) -> str:
    return TYPE_BY_NAME.get(name, DEFAULT_TYPE)


# --- Event-level classification (lesson vs personal) --------------------------
# Personal/outing keywords win first (e.g. „Немски", „бал" → лично, по инструкция).
_PERSONAL_RE = re.compile(
    r"(немск|бал\b|networking|нетуъркинг|таро|рожден|парти|кино|излиз|"
    r"гадж|почивк|ваканц|кафе|вечеря|обяд с|среща с)",
    re.IGNORECASE,
)
# Lesson markers: starts with „Урок" OR carries a subject token OR a grade.
_SUBJECT_RE = re.compile(
    r"\b(бел|мат|матем|хими|физик|ае|англ|англий|био|географ|истори|"
    r"информатик|програмиран|гр\b)\b",
    re.IGNORECASE,
)
_GRADE_RE = re.compile(r"\d+\s*кл|онлайн|online", re.IGNORECASE)
# University markers (FMI): тест/изпит/контролно/лекция/упражнение/курсова/сесия.
_UNI_RE = re.compile(
    r"(тест|изпит|контролно|колоквиум|лекци|упражнени|семинар|защит|"
    r"курсов|проект|сесия|fmi|фми)",
    re.IGNORECASE,
)
# Notes/reminders that look like work but aren't a lesson slot.
_NOTE_RE = re.compile(
    r"(пиши|може и|напомни|плат[еи]|изпрати|обади|deadline|такса|ново дете)",
    re.IGNORECASE,
)


def classify_event(summary: str, cal_type: str | None = None) -> str:
    """Return 'lesson' | 'university' | 'personal' | 'other' from the title.

    Only the „Work" calendar (cal_type='work') is private tutoring → short
    name-like titles там са уроци. Other calendars are mixed: уни (тест/изпит/
    лекция), лично (немски/бал/излизане), or разни.
    """
    s = (summary or "").strip()
    if not s:
        return "other"
    if _PERSONAL_RE.search(s):
        return "personal"
    # University events live in the non-work calendars.
    if cal_type != "work" and _UNI_RE.search(s):
        return "university"
    if s.lower().startswith("урок") or _SUBJECT_RE.search(s) or _GRADE_RE.search(s):
        return "lesson"
    if _NOTE_RE.search(s):
        return "other"
    # Tutoring calendar: a short name-like title is almost always a lesson slot.
    if cal_type == "work" and len(s.split()) <= 4:
        return "lesson"
    return "other"


# --- Aggregated agenda --------------------------------------------------------


async def enabled_calendars() -> list[dict[str, Any]]:
    """The iOS-imported calendars we read (with owner/type attached)."""
    out = []
    for c in await gcal.list_calendars():
        if not _is_ios(c):
            continue
        name = c.get("summary", "")
        out.append(
            {"id": c["id"], "name": name, "owner": owner_of(name), "cal_type": type_of(name)}
        )
    return out


def _parse_day(value: str, *, end: bool = False) -> datetime:
    """Parse 'YYYY-MM-DD' (or ISO) as a LOCAL day boundary → tz-aware UTC.

    `end=True` snaps a bare date to the END of that day (23:59:59).
    """
    from bogi.tz import local_tz

    v = (value or "").strip().replace(" ", "T")
    if "T" in v:
        dt = datetime.fromisoformat(v.split("+")[0].rstrip("Z"))
    else:
        dt = datetime.fromisoformat(v)
        if end:
            dt = dt.replace(hour=23, minute=59, second=59)
    return dt.replace(tzinfo=local_tz()).astimezone(UTC)


async def agenda(
    days: int = 7,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    owner: str | None = None,
    cal_type: str | None = None,
    event_class: str | None = None,
) -> list[dict[str, Any]]:
    """Events from all enabled iOS calendars, tagged + merged + time-sorted.

    Window: `date_from`..`date_to` (ISO/'YYYY-MM-DD', LOCAL, may be in the past)
    if given, else now..now+`days`. Filters: owner ('bogdan'|'martin'),
    cal_type, event_class ('lesson'|'personal'|'other').
    """
    cals = await enabled_calendars()
    if owner:
        cals = [c for c in cals if c["owner"] == owner]
    if cal_type:
        cals = [c for c in cals if c["cal_type"] == cal_type]

    if date_from or date_to:
        now = _parse_day(date_from) if date_from else datetime.now(UTC)
        end = _parse_day(date_to, end=True) if date_to else now + timedelta(days=days)
    else:
        now = datetime.now(UTC)
        end = now + timedelta(days=days)
    items: list[dict[str, Any]] = []
    for c in cals:
        try:
            evs = await gcal.list_events(
                time_min=now, time_max=end, calendar_id=c["id"], max_results=50
            )
        except Exception:
            continue
        for e in evs:
            ec = classify_event(e.get("summary", ""), cal_type=c["cal_type"])
            if event_class and ec != event_class:
                continue
            items.append(
                {
                    "summary": e.get("summary", ""),
                    "start": e.get("start", ""),
                    "end": e.get("end", ""),
                    "calendar": c["name"],
                    "owner": c["owner"],
                    "cal_type": c["cal_type"],
                    "event_class": ec,
                }
            )
    items.sort(key=lambda x: x["start"])
    return items


async def count(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    days: int = 7,
    owner: str | None = None,
    cal_type: str | None = None,
    event_class: str | None = None,
) -> dict[str, Any]:
    """Count events in a period (past or future), broken down by class/owner/calendar."""
    items = await agenda(
        days=days, date_from=date_from, date_to=date_to,
        owner=owner, cal_type=cal_type, event_class=event_class,
    )
    by_class: dict[str, int] = {}
    by_owner: dict[str, int] = {}
    by_calendar: dict[str, int] = {}
    for e in items:
        by_class[e["event_class"]] = by_class.get(e["event_class"], 0) + 1
        by_owner[e["owner"]] = by_owner.get(e["owner"], 0) + 1
        by_calendar[e["calendar"]] = by_calendar.get(e["calendar"], 0) + 1
    return {
        "total": len(items),
        "by_event_class": by_class,
        "by_owner": by_owner,
        "by_calendar": by_calendar,
    }
