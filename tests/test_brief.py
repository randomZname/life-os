"""Tests for the deterministic morning brief (no network / no DB).

`asyncio_mode = "auto"` (pyproject) runs async test functions automatically,
so no explicit @pytest.mark.asyncio is needed.
"""

from __future__ import annotations

from bogi.modules import brief


class FakeFMI:
    """Minimal stand-in for a live FMIScraper exposing get_upcoming_events."""

    def __init__(self, deadlines=None, raise_exc: bool = False):
        self._deadlines = deadlines or []
        self._raise = raise_exc

    async def get_upcoming_events(self):
        if self._raise:
            raise RuntimeError("moodle down")
        return self._deadlines


def _agenda_events():
    return [
        {
            "summary": "Васко 2 кл",
            "start": "2026-06-03T15:00:00+03:00",
            "end": "2026-06-03T16:00:00+03:00",
            "calendar": "Work",
            "owner": "bogdan",
            "cal_type": "work",
            "event_class": "lesson",
        },
        {
            "summary": "Лекция ООП",
            "start": "2026-06-03T10:00:00+03:00",
            "end": "2026-06-03T12:00:00+03:00",
            "calendar": "Untitled",
            "owner": "bogdan",
            "cal_type": "other",
            "event_class": "university",
        },
        {
            "summary": "Футбол",
            "start": "2026-06-03T18:00:00+03:00",
            "end": "2026-06-03T19:00:00+03:00",
            "calendar": "Марто",
            "owner": "martin",
            "cal_type": "other",
            "event_class": "personal",
        },
    ]


def _deadlines():
    return [
        {
            "title": "ДЗ 3",
            "url": "https://learn.fmi.uni-sofia.bg/mod/assign/view.php?id=1",
            "time_text": "Утре, 23:59",
            "course": "СДП",
            "kind": "assignment",
        },
        {
            "title": "Контролно 2",
            "url": "https://learn.fmi.uni-sofia.bg/mod/quiz/view.php?id=2",
            "time_text": "Петък, 10:00",
            "course": "ДАА",
            "kind": "quiz",
        },
    ]


async def test_normal_case_formats_and_groups(monkeypatch):
    async def fake_agenda(*args, **kwargs):
        return _agenda_events()

    monkeypatch.setattr(brief.calendars, "agenda", fake_agenda)

    out = await brief.compose_brief(1, FakeFMI(_deadlines()))

    # Agenda block present with grouped sections.
    assert "📅 Днес" in out
    assert "📚 Уроци" in out
    assert "🎓 Университет" in out
    assert "🎉 Лично" in out
    assert "Лекция ООП" in out
    assert "⏰ 10:00" in out  # time pulled from ISO start

    # Deadlines block mirrors SYSTEM_PROMPT format and groups by kind.
    assert "📅 Предстоящи задачи" in out
    assert "📝 ДОМАШНИ:" in out
    assert "📋 ТЕСТОВЕ / КОНТРОЛНИ:" in out
    assert "СДП — ДЗ 3" in out
    assert "ДАА — Контролно 2" in out
    assert "⏰ Утре, 23:59" in out


async def test_martin_events_are_distinguishable(monkeypatch):
    async def fake_agenda(*args, **kwargs):
        return _agenda_events()

    monkeypatch.setattr(brief.calendars, "agenda", fake_agenda)

    out = await brief.compose_brief(1, FakeFMI([]))
    assert "Мартин" in out  # brother's event labelled
    # The Martin personal line carries the label.
    assert "Футбол" in out


async def test_empty_both_returns_friendly_line(monkeypatch):
    async def fake_agenda(*args, **kwargs):
        return []

    monkeypatch.setattr(brief.calendars, "agenda", fake_agenda)

    out = await brief.compose_brief(1, FakeFMI([]))
    assert out == "Днес нямаш нищо записано. 🎉"


async def test_agenda_failure_still_yields_deadlines(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("gcal down")

    monkeypatch.setattr(brief.calendars, "agenda", boom)

    out = await brief.compose_brief(1, FakeFMI(_deadlines()))
    # No exception; deadlines still present, agenda block absent.
    assert "📅 Предстоящи задачи" in out
    assert "СДП — ДЗ 3" in out
    assert "📅 Днес" not in out


async def test_deadlines_failure_still_yields_agenda(monkeypatch):
    async def fake_agenda(*args, **kwargs):
        return _agenda_events()

    monkeypatch.setattr(brief.calendars, "agenda", fake_agenda)

    out = await brief.compose_brief(1, FakeFMI(raise_exc=True))
    # No exception; agenda still present, deadlines block absent.
    assert "📅 Днес" in out
    assert "📚 Уроци" in out
    assert "📅 Предстоящи задачи" not in out
