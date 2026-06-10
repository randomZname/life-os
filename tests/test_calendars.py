"""Pure-logic tests for calendar classification (no network)."""

from __future__ import annotations

from datetime import UTC

from bogi.modules import calendars as cal


def test_classify_personal_keywords():
    assert cal.classify_event("Немски") == "personal"
    assert cal.classify_event("Таро+networking") == "personal"
    assert cal.classify_event("рожден ден на Иван") == "personal"


def test_classify_lessons():
    assert cal.classify_event("Урок Малена") == "lesson"
    assert cal.classify_event("Васко 2 кл", cal_type="work") == "lesson"
    assert cal.classify_event("Алекс 7 кл online", cal_type="work") == "lesson"
    assert cal.classify_event("Енеха БЕЛ") == "lesson"  # subject token
    assert cal.classify_event("Виктор", cal_type="work") == "lesson"  # short name in work cal


def test_classify_notes_and_other():
    assert cal.classify_event("Менторката може и по рано, пиши й", cal_type="work") == "other"
    assert cal.classify_event("Some long unrelated description here", cal_type="personal") == "other"


def test_owner_and_type_mapping():
    assert cal.owner_of("Марто") == "martin"
    assert cal.owner_of("Work") == "bogdan"
    assert cal.type_of("Work") == "work"
    # Only Work is work; the other calendars are mixed → other (per-event).
    assert cal.type_of("Untitled") == "other"
    assert cal.type_of("Unnamed event") == "other"
    assert cal.type_of("Whatever") == "other"


def test_classify_university_in_nonwork():
    assert cal.classify_event("БД тест", cal_type="other") == "university"
    assert cal.classify_event("Фрактали тест", cal_type="other") == "university"
    assert cal.classify_event("Лекция ООП") == "university"
    # In the Work (tutoring) calendar, uni markers don't apply.
    assert cal.classify_event("Иван тест", cal_type="work") == "lesson"


def test_parse_day_local_to_utc():
    # Sofia is UTC+3 in June → local midnight 2026-06-05 == 2026-06-04T21:00Z
    start = cal._parse_day("2026-06-05")
    assert start.tzinfo == UTC
    assert start.hour == 21 and start.day == 4
    end = cal._parse_day("2026-06-05", end=True)
    assert end > start
