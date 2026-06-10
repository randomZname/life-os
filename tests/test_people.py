"""Personal CRM (bogi.modules.people) tests.

DB-touching: requires Postgres up. Each test uses a sentinel user_id (BIG
random) and hard-deletes that user's people in a try/finally (interactions
cascade via the FK ondelete=CASCADE).

Coverage:
- add_person + find_person (by name and by alias)
- update-on-duplicate-name (same name, case-insensitive -> updates, same id)
- log_interaction sets last_contact_at and auto-creates a missing person
- due_followups stale detection (never-contacted + old contact)
- due_followups upcoming-birthday detection (month/day only)
"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest

from bogi.modules import people
from bogi.tz import now_local


def _fake_user() -> int:
    return 9_000_000_000 + random.randint(1, 10_000_000)


async def _cleanup(user_id: int) -> None:
    """Hard-delete all people for this synthetic user (interactions cascade)."""
    from sqlalchemy import delete

    from bogi.db import get_session
    from bogi.models import Person

    async with get_session() as session:
        await session.execute(delete(Person).where(Person.user_id == user_id))


@pytest.mark.asyncio
async def test_add_and_find_by_name_and_alias():
    uid = _fake_user()
    try:
        pid = await people.add_person(
            uid,
            "Martin Petrov",
            relation="brother",
            aliases=["Марто", "Mart"],
            birthday="2005-09-12",
            notes="younger brother",
        )
        assert isinstance(pid, int) and pid > 0

        # case-insensitive contains match on name
        by_name = await people.find_person(uid, "martin")
        assert by_name is not None
        assert by_name["id"] == pid
        assert by_name["relation"] == "brother"
        assert by_name["birthday"] == "2005-09-12"
        assert "Марто" in by_name["aliases"]

        # match on alias
        by_alias = await people.find_person(uid, "сами")
        assert by_alias is not None and by_alias["id"] == pid

        # no match -> None
        assert await people.find_person(uid, "nonexistent-zzz") is None
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_update_on_duplicate_name():
    uid = _fake_user()
    try:
        first = await people.add_person(uid, "Ivan Ivanov", relation="friend")
        # same name, different case -> update existing, return same id
        second = await people.add_person(
            uid, "ivan ivanov", relation="student", notes="maths tutoring"
        )
        assert second == first

        found = await people.find_person(uid, "Ivan Ivanov")
        assert found is not None
        assert found["relation"] == "student"
        assert found["notes"] == "maths tutoring"

        # only one person for this user
        listed = await people.list_people(uid)
        assert len(listed) == 1
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_list_people_relation_filter_and_sort():
    uid = _fake_user()
    try:
        await people.add_person(uid, "Zoe", relation="friend")
        await people.add_person(uid, "Anna", relation="friend")
        await people.add_person(uid, "Boris", relation="professor")

        friends = await people.list_people(uid, relation="friend")
        names = [p["name"] for p in friends]
        assert names == ["Anna", "Zoe"]  # name-sorted

        everyone = await people.list_people(uid)
        assert [p["name"] for p in everyone] == ["Anna", "Boris", "Zoe"]
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_log_interaction_sets_last_contact_and_autocreates():
    uid = _fake_user()
    try:
        await people.add_person(uid, "Maria", relation="friend")

        when = (now_local() - timedelta(days=2)).replace(microsecond=0)
        res = await people.log_interaction(
            uid,
            "maria",
            "Coffee and chat",
            channel="in_person",
            occurred_at=when.isoformat(),
        )
        assert res["logged"] is True
        assert res["person_name"] == "Maria"

        found = await people.find_person(uid, "Maria")
        assert found["last_contact_at"] is not None
        assert found["last_contact_at"].startswith(when.date().isoformat())

        # auto-create on unknown person
        res2 = await people.log_interaction(
            uid, "Brand New Guy", "Met at the gym"
        )
        assert res2["logged"] is True
        assert res2["person_id"] > 0
        created = await people.find_person(uid, "Brand New Guy")
        assert created is not None
        assert created["last_contact_at"] is not None
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_due_followups_stale_detection():
    uid = _fake_user()
    try:
        # never contacted -> stale
        await people.add_person(uid, "NeverTalked", relation="contact")

        # contacted long ago -> stale
        await people.add_person(uid, "OldFriend", relation="friend")
        old = (now_local() - timedelta(days=40)).replace(microsecond=0)
        await people.log_interaction(
            uid, "OldFriend", "ping", occurred_at=old.isoformat()
        )

        # contacted recently -> NOT stale
        await people.add_person(uid, "RecentPal", relation="friend")
        recent = (now_local() - timedelta(days=2)).replace(microsecond=0)
        await people.log_interaction(
            uid, "RecentPal", "ping", occurred_at=recent.isoformat()
        )

        out = await people.due_followups(uid, stale_days=21)
        stale_names = {p["name"] for p in out["stale"]}
        assert "NeverTalked" in stale_names
        assert "OldFriend" in stale_names
        assert "RecentPal" not in stale_names

        for entry in out["stale"]:
            if entry["name"] == "OldFriend":
                assert entry["days_since"] >= 40
            if entry["name"] == "NeverTalked":
                assert entry["last_contact_at"] is None
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_due_followups_upcoming_birthday():
    uid = _fake_user()
    try:
        today = now_local().date()
        soon = today + timedelta(days=5)
        far = today + timedelta(days=60)

        # use a non-current year so we exercise month/day-only comparison
        await people.add_person(
            uid, "BdaySoon", birthday=soon.replace(year=1990).isoformat()
        )
        await people.add_person(
            uid, "BdayFar", birthday=far.replace(year=1990).isoformat()
        )

        out = await people.due_followups(uid, birthday_within=14)
        names = {b["name"] for b in out["birthdays"]}
        assert "BdaySoon" in names
        assert "BdayFar" not in names

        for b in out["birthdays"]:
            if b["name"] == "BdaySoon":
                assert b["in_days"] == 5
    finally:
        await _cleanup(uid)
