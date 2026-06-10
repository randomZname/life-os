"""Google Calendar client — read + limited write (create + quick_add only).

OAuth flow on first run:
    `python -m bogi gcal-auth` отваря браузър → ти оторизираш → token cache в
    `data/gcal/token.json`. Refresh става автоматично от lib-а след това.

Framework-agnostic — никакъв pydantic_ai import тук. Agent registration е в
`bogi/agent.py`.

Write surface: **create + quick_add** (user-driven, ungated) + **update_event**
(agent-initiated, минава през approval queue — V2.B). Delete все още НЕ е
имплементиран (deferred, докато update cycle-ът се докаже live).

Lethal Trifecta анализ за текущия set:
  - Private data: да (твой календар)
  - Untrusted content: не (write е user-driven през Telegram message,
    не event-driven от inbound mail/web)
  - External comm: да (write към Google)
  ⇒ trifecta-та е счупена в "untrusted content" leg. Safe без approval.
  ⚠️ Това **се чупи** в момента, в който агентът започне да реагира на
  inbound съдържание (Gmail body, Moodle forum post, scraped page) — тогава
  approval queue става задължителен преди да позволиш write tool-ите.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from bogi.config import settings

logger = logging.getLogger(__name__)

# Read + event create/edit (no calendar-list management).
# Bumping this requires re-running OAuth — cached token is locked to old scopes.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def _load_credentials() -> Credentials:
    """Read cached token; refresh if expired; raise if not authorized yet."""
    token_path: Path = settings.gcal_token
    secret_path: Path = settings.gcal_client_secret

    if not secret_path.exists():
        raise FileNotFoundError(
            f"client_secret.json not found at {secret_path}. "
            "Download OAuth client from Google Cloud Console (Desktop type) "
            "and save it there."
        )

    if not token_path.exists():
        raise RuntimeError(
            "Google Calendar not authorized yet. Run `python -m bogi gcal-auth` "
            "to authorize (opens browser)."
        )

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
            logger.info("Google Calendar token refreshed")
        else:
            raise RuntimeError(
                "Google Calendar token is invalid and cannot refresh. "
                "Re-run `python -m bogi gcal-auth`."
            )
    return creds


def authorize_interactive() -> Path:
    """Run OAuth flow: opens local browser; stores token in `data/gcal/token.json`.

    Safe to re-run — overwrites existing token.
    """
    secret_path: Path = settings.gcal_client_secret
    token_path: Path = settings.gcal_token
    if not secret_path.exists():
        raise FileNotFoundError(
            f"client_secret.json not found at {secret_path}. "
            "Download from Google Cloud Console first."
        )

    token_path.parent.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    logger.info("Google Calendar authorized; token saved to %s", token_path)
    return token_path


def _build_service():
    """Construct calendar API service (sync — wrap with asyncio.to_thread)."""
    creds = _load_credentials()
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _iso_utc(dt: datetime) -> str:
    """Google expects RFC3339 timestamps with Z or +HH:MM offset."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Strip Google's event payload down to the fields the agent needs."""
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "id": event.get("id", ""),
        "summary": event.get("summary", ""),
        "description": event.get("description", "") or "",
        "location": event.get("location", "") or "",
        "start": start.get("dateTime") or start.get("date") or "",
        "end": end.get("dateTime") or end.get("date") or "",
        "all_day": "date" in start and "dateTime" not in start,
        "url": event.get("htmlLink", ""),
        "status": event.get("status", ""),
        "organizer_email": (event.get("organizer") or {}).get("email", ""),
        "attendees_count": len(event.get("attendees", []) or []),
        "recurring_event_id": event.get("recurringEventId", "") or "",
    }


def _list_events_sync(
    *,
    time_min: datetime,
    time_max: datetime,
    calendar_id: str,
    query: str | None,
    max_results: int,
) -> list[dict[str, Any]]:
    service = _build_service()
    try:
        resp = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=_iso_utc(time_min),
                timeMax=_iso_utc(time_max),
                singleEvents=True,
                orderBy="startTime",
                maxResults=max_results,
                q=query,
                # Return start/end in the user's local zone (e.g. ...T16:00:00+03:00)
                # instead of UTC ('...Z'), so the agent reads/reports local time —
                # matching the write side which interprets bare times as local.
                timeZone=settings.gcal_timezone,
            )
            .execute()
        )
    except HttpError as exc:
        logger.exception("Google Calendar API error")
        raise RuntimeError(f"Calendar API error: {exc}") from exc

    return [_normalize_event(e) for e in resp.get("items", [])]


async def list_events(
    *,
    time_min: datetime,
    time_max: datetime,
    calendar_id: str | None = None,
    query: str | None = None,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """Async wrapper around the sync Google client."""
    cal = calendar_id or settings.gcal_calendar_id
    return await asyncio.to_thread(
        _list_events_sync,
        time_min=time_min,
        time_max=time_max,
        calendar_id=cal,
        query=query,
        max_results=max_results,
    )


async def today(calendar_id: str | None = None) -> list[dict[str, Any]]:
    """Events for the current calendar day in `settings.gcal_timezone`."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return await list_events(
        time_min=start, time_max=end, calendar_id=calendar_id, max_results=50
    )


async def upcoming(days: int = 7, calendar_id: str | None = None) -> list[dict[str, Any]]:
    """Events from now until `now + days`."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    return await list_events(
        time_min=now, time_max=end, calendar_id=calendar_id, max_results=100
    )


async def search(
    query: str, days: int = 30, calendar_id: str | None = None
) -> list[dict[str, Any]]:
    """Free-text search across summary/description/location/attendee."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    return await list_events(
        time_min=now,
        time_max=end,
        calendar_id=calendar_id,
        query=query,
        max_results=50,
    )


def _list_calendars_sync() -> list[dict[str, Any]]:
    service = _build_service()
    resp = service.calendarList().list().execute()
    return [
        {
            "id": c["id"],
            "summary": c.get("summary", ""),
            "primary": c.get("primary", False),
            "timezone": c.get("timeZone", ""),
            "access_role": c.get("accessRole", ""),
        }
        for c in resp.get("items", [])
    ]


async def list_calendars() -> list[dict[str, Any]]:
    """List all calendars the user has access to."""
    return await asyncio.to_thread(_list_calendars_sync)


def _get_event_sync(event_id: str, calendar_id: str) -> dict[str, Any]:
    service = _build_service()
    try:
        ev = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as exc:
        logger.exception("Google Calendar get_event failed")
        raise RuntimeError(f"Calendar API error: {exc}") from exc
    return _normalize_event(ev)


async def get_event(event_id: str, calendar_id: str | None = None) -> dict[str, Any]:
    """Fetch a single event (normalized) by id."""
    cal = calendar_id or settings.gcal_calendar_id
    return await asyncio.to_thread(_get_event_sync, event_id, cal)


# ---- Write ops (limited surface — create + quick_add only) -------------------


def _parse_dt(value: str) -> dict[str, Any]:
    """Convert a user-supplied datetime string to a Google event time entry.

    Accepts:
      - ISO datetime: "2026-05-20T15:00:00"  → timed entry with gcal_timezone
      - Date only:    "2026-05-20"           → all-day entry

    All times are treated as LOCAL wall-clock in `settings.gcal_timezone`. The
    agent sometimes appends a 'Z' / offset (UTC) for a time the user gave in
    local terms — Google would then honour the offset and ignore `timeZone`,
    shifting the event by the UTC offset (e.g. 18:00Z → 21:00 in Sofia). We
    strip any trailing zone designator so the wall-clock time stands as given.
    """
    value = value.strip()
    if "T" in value or " " in value:
        normalized = value.replace(" ", "T")
        normalized = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", normalized).strip()
        return {"dateTime": normalized, "timeZone": settings.gcal_timezone}
    return {"date": value}


def _create_event_sync(
    *,
    summary: str,
    start: str,
    end: str,
    location: str,
    description: str,
    calendar_id: str,
) -> dict[str, Any]:
    service = _build_service()
    body: dict[str, Any] = {
        "summary": summary,
        "start": _parse_dt(start),
        "end": _parse_dt(end),
    }
    if location:
        body["location"] = location
    if description:
        body["description"] = description

    try:
        created = (
            service.events().insert(calendarId=calendar_id, body=body).execute()
        )
    except HttpError as exc:
        logger.exception("Google Calendar create_event failed")
        raise RuntimeError(f"Calendar API error: {exc}") from exc

    return _normalize_event(created)


async def create_event(
    *,
    summary: str,
    start: str,
    end: str,
    location: str = "",
    description: str = "",
    calendar_id: str | None = None,
) -> dict[str, Any]:
    """Create a Google Calendar event.

    Args:
        summary: event title
        start / end: ISO datetime (`2026-05-20T15:00:00`) or date (`2026-05-20`)
        location: optional venue
        description: optional notes
        calendar_id: defaults to `settings.gcal_calendar_id` ("primary")
    """
    cal = calendar_id or settings.gcal_calendar_id
    return await asyncio.to_thread(
        _create_event_sync,
        summary=summary,
        start=start,
        end=end,
        location=location,
        description=description,
        calendar_id=cal,
    )


def _update_event_sync(
    *,
    event_id: str,
    fields: dict[str, Any],
    calendar_id: str,
) -> dict[str, Any]:
    service = _build_service()
    try:
        updated = (
            service.events()
            .patch(calendarId=calendar_id, eventId=event_id, body=fields)
            .execute()
        )
    except HttpError as exc:
        logger.exception("Google Calendar update_event failed")
        raise RuntimeError(f"Calendar API error: {exc}") from exc

    return _normalize_event(updated)


async def update_event(
    *,
    event_id: str,
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    description: str | None = None,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    """Partially update an existing Google Calendar event (patch semantics).

    Only the provided (non-None) fields are sent — everything else is left
    untouched. Agent-initiated calls go through the approval queue (V2.B).

    Args:
        event_id: id of the event to patch (resolve via search/list first)
        summary: new title
        start / end: ISO datetime (`2026-05-20T15:00:00`) or date (`2026-05-20`)
        location: new venue
        description: new notes
        calendar_id: defaults to `settings.gcal_calendar_id` ("primary")

    Raises:
        ValueError: if no fields are provided (all None).
    """
    body: dict[str, Any] = {}
    if summary is not None:
        body["summary"] = summary
    if start is not None:
        body["start"] = _parse_dt(start)
    if end is not None:
        body["end"] = _parse_dt(end)
    if location is not None:
        body["location"] = location
    if description is not None:
        body["description"] = description

    if not body:
        raise ValueError("update_event: nothing to update")

    cal = calendar_id or settings.gcal_calendar_id
    return await asyncio.to_thread(
        _update_event_sync,
        event_id=event_id,
        fields=body,
        calendar_id=cal,
    )


def _delete_event_sync(*, event_id: str, calendar_id: str) -> dict[str, Any]:
    service = _build_service()
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as exc:
        logger.exception("Google Calendar delete_event failed")
        raise RuntimeError(f"Calendar API error: {exc}") from exc
    return {"id": event_id, "deleted": True}


async def delete_event(
    *,
    event_id: str,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    """Delete a Google Calendar event by id. Irreversible — gate behind approval."""
    cal = calendar_id or settings.gcal_calendar_id
    return await asyncio.to_thread(
        _delete_event_sync, event_id=event_id, calendar_id=cal
    )


def _quick_add_sync(text: str, calendar_id: str) -> dict[str, Any]:
    service = _build_service()
    try:
        created = (
            service.events().quickAdd(calendarId=calendar_id, text=text).execute()
        )
    except HttpError as exc:
        logger.exception("Google Calendar quickAdd failed")
        raise RuntimeError(f"Calendar API error: {exc}") from exc
    return _normalize_event(created)


async def quick_add(text: str, calendar_id: str | None = None) -> dict[str, Any]:
    """Natural-language event creation via Google's parser.

    Example inputs:
      - "Обяд със Стефан утре в 12:30"
      - "Dentist Friday 3pm"
      - "Лекция по бази от данни понеделник 10:15-12:00"

    Note: Google's parser is best with English. BG works but mixed results.
    For precise control, prefer `create_event(...)` with explicit ISO times.
    """
    cal = calendar_id or settings.gcal_calendar_id
    return await asyncio.to_thread(_quick_add_sync, text=text, calendar_id=cal)
