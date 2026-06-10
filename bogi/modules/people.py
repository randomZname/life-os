"""Personal CRM: people in Богдан's life + interaction logging + follow-up nudges.

Framework-agnostic (no pydantic_ai / litellm). All functions are async, take a
`user_id: int` first argument, and return JSON-friendly dicts (dates/datetimes
serialized as ISO strings). The lead wires these as agent tools.

Backing tables (see bogi.models.schema): `Person` and `Interaction`.

Public API:
    add_person(user_id, name, *, relation, birthday, aliases, notes) -> int
    find_person(user_id, query) -> dict | None
    list_people(user_id, *, relation) -> list[dict]
    log_interaction(user_id, person_query, summary, *, channel, occurred_at) -> dict
    due_followups(user_id, *, stale_days, birthday_within) -> dict
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select

from bogi.db import get_session
from bogi.models import Interaction, Person
from bogi.tz import now_local

# --- helpers -----------------------------------------------------------------

def _parse_date(value: str | None) -> date | None:
    """Parse an ISO 'YYYY-MM-DD' string into a date, or None."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _parse_dt(value: str | None) -> datetime:
    """Parse an ISO datetime string into a naive datetime (default: now local).

    Persistence uses naive datetimes (project convention). We strip any tz info
    so comparisons against DB-stored naive datetimes stay consistent.
    """
    if value is None:
        dt = now_local()
    elif isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _iso(value: date | datetime | None) -> str | None:
    """Serialize a date/datetime to an ISO string, or None."""
    if value is None:
        return None
    return value.isoformat()


def _person_dict(p: Person) -> dict:
    """Full JSON-friendly view of a Person used by find_person."""
    return {
        "id": p.id,
        "name": p.name,
        "relation": p.relation,
        "birthday": _iso(p.birthday),
        "notes": p.notes,
        "last_contact_at": _iso(p.last_contact_at),
        "aliases": list(p.aliases) if p.aliases else [],
    }


# --- public API --------------------------------------------------------------

async def add_person(
    user_id: int,
    name: str,
    *,
    relation: str | None = None,
    birthday: str | None = None,
    aliases: list[str] | None = None,
    notes: str | None = None,
) -> int:
    """Add or update a person.

    If a non-archived person with the same name (case-insensitive) already
    exists for this user, update its provided fields and return its id.
    Otherwise insert a new person. Returns the person id.
    """
    bday = _parse_date(birthday)
    async with get_session() as session:
        existing = (
            await session.execute(
                select(Person).where(
                    Person.user_id == user_id,
                    Person.archived.is_(False),
                    func.lower(Person.name) == name.lower(),
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            if relation is not None:
                existing.relation = relation
            if bday is not None:
                existing.birthday = bday
            if aliases is not None:
                existing.aliases = list(aliases)
            if notes is not None:
                existing.notes = notes
            existing.updated_at = datetime.utcnow()
            await session.flush()
            return existing.id

        person = Person(
            user_id=user_id,
            name=name,
            relation=relation,
            birthday=bday,
            aliases=list(aliases) if aliases is not None else None,
            notes=notes,
        )
        session.add(person)
        await session.flush()
        return person.id


async def find_person(user_id: int, query: str) -> dict | None:
    """Find a person by case-insensitive contains-match on name OR any alias.

    Returns {id,name,relation,birthday,notes,last_contact_at,aliases} or None.
    """
    q = (query or "").strip().lower()
    if not q:
        return None
    async with get_session() as session:
        people = (
            await session.execute(
                select(Person).where(
                    Person.user_id == user_id,
                    Person.archived.is_(False),
                )
            )
        ).scalars().all()

    # Name contains-match first (deterministic: prefer exact, then shortest name).
    name_hits = [p for p in people if q in p.name.lower()]
    if name_hits:
        name_hits.sort(key=lambda p: (p.name.lower() != q, len(p.name)))
        return _person_dict(name_hits[0])

    # Then alias contains-match.
    for p in people:
        for alias in (p.aliases or []):
            if q in str(alias).lower():
                return _person_dict(p)

    return None


async def list_people(user_id: int, *, relation: str | None = None) -> list[dict]:
    """List non-archived people (optional relation filter), name-sorted."""
    async with get_session() as session:
        stmt = select(Person).where(
            Person.user_id == user_id,
            Person.archived.is_(False),
        )
        if relation is not None:
            stmt = stmt.where(Person.relation == relation)
        stmt = stmt.order_by(func.lower(Person.name))
        people = (await session.execute(stmt)).scalars().all()
        return [_person_dict(p) for p in people]


async def log_interaction(
    user_id: int,
    person_query: str,
    summary: str,
    *,
    channel: str | None = None,
    occurred_at: str | None = None,
) -> dict:
    """Log an interaction with a person.

    Finds the person by `person_query`; if none matches, auto-creates a minimal
    person using `person_query` as the name. Inserts an Interaction and bumps
    the person's `last_contact_at` to `occurred_at` (default: now).

    Returns {person_id, person_name, logged: True}.
    """
    when = _parse_dt(occurred_at)
    q = (person_query or "").strip().lower()
    async with get_session() as session:
        person: Person | None = None
        if q:
            people = (
                await session.execute(
                    select(Person).where(
                        Person.user_id == user_id,
                        Person.archived.is_(False),
                    )
                )
            ).scalars().all()

            name_hits = [p for p in people if q in p.name.lower()]
            if name_hits:
                name_hits.sort(key=lambda p: (p.name.lower() != q, len(p.name)))
                person = name_hits[0]
            else:
                for p in people:
                    for alias in (p.aliases or []):
                        if q in str(alias).lower():
                            person = p
                            break
                    if person is not None:
                        break

        if person is None:
            person = Person(
                user_id=user_id,
                name=(person_query or "").strip() or "Unknown",
            )
            session.add(person)
            await session.flush()

        session.add(
            Interaction(
                person_id=person.id,
                occurred_at=when,
                channel=channel,
                summary=summary,
            )
        )
        person.last_contact_at = when
        person.updated_at = datetime.utcnow()
        await session.flush()

        return {
            "person_id": person.id,
            "person_name": person.name,
            "logged": True,
        }


async def due_followups(
    user_id: int,
    *,
    stale_days: int = 21,
    birthday_within: int = 14,
) -> dict:
    """Surface people who need a follow-up.

    Returns:
        {
          "stale": [ people not contacted in >= stale_days days (or never),
                     each {id,name,relation,last_contact_at,days_since} ],
          "birthdays": [ people whose birthday falls within the next
                         birthday_within days (month/day only),
                         each {id,name,birthday,in_days} ],
        }
    """
    today = now_local().date()

    async with get_session() as session:
        people = (
            await session.execute(
                select(Person).where(
                    Person.user_id == user_id,
                    Person.archived.is_(False),
                )
            )
        ).scalars().all()

    stale: list[dict] = []
    birthdays: list[dict] = []

    for p in people:
        # --- stale detection ---
        if p.last_contact_at is None:
            stale.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "relation": p.relation,
                    "last_contact_at": None,
                    "days_since": None,
                }
            )
        else:
            last_date = p.last_contact_at.date()
            days_since = (today - last_date).days
            if days_since >= stale_days:
                stale.append(
                    {
                        "id": p.id,
                        "name": p.name,
                        "relation": p.relation,
                        "last_contact_at": _iso(p.last_contact_at),
                        "days_since": days_since,
                    }
                )

        # --- upcoming birthday (month/day only) ---
        if p.birthday is not None:
            in_days = _days_until_birthday(today, p.birthday)
            if 0 <= in_days <= birthday_within:
                birthdays.append(
                    {
                        "id": p.id,
                        "name": p.name,
                        "birthday": _iso(p.birthday),
                        "in_days": in_days,
                    }
                )

    stale.sort(
        key=lambda d: (d["days_since"] is not None, -(d["days_since"] or 0))
    )
    birthdays.sort(key=lambda d: d["in_days"])

    return {"stale": stale, "birthdays": birthdays}


def _days_until_birthday(today: date, birthday: date) -> int:
    """Days from `today` until the next occurrence of birthday's month/day.

    Ignores the birthday's year. Returns 0 if the birthday is today. Handles
    Feb-29 birthdays by falling back to Feb-28 in non-leap years.
    """
    def _occurrence(year: int) -> date:
        try:
            return date(year, birthday.month, birthday.day)
        except ValueError:
            # Feb 29 in a non-leap year -> treat as Feb 28.
            return date(year, birthday.month, 28)

    this_year = _occurrence(today.year)
    if this_year >= today:
        return (this_year - today).days
    next_year = _occurrence(today.year + 1)
    return (next_year - today).days
