"""JSON API for the BogiAgent web dashboard (Phase 5, CONTRACT 3).

Track A: HTTP adapters over the existing framework-agnostic modules + the shared
agent. NO auth here — the lead's ``AuthMiddleware`` guards every path. NO app
object here — the lead assembles it in ``bogi/web/app.py``. This file only
exports ``api_routes()`` returning Starlette ``Route`` objects.

Design rules (CONTRACT 3):
- ``user_id = settings.allowed_user_ids[0]``; if empty → ``{"error": "no user
  configured"}``.
- The shared agent lives at ``request.app.state.agent`` (a ``BogiAgent``). Used
  for ``/api/chat`` and for the fmi-backed endpoints (``agent.fmi``).
- Every handler wraps its body in try/except and returns ``{"error": str(exc)}``
  with HTTP 200 on failure (frontend renders it gracefully), EXCEPT ``/api/chat``
  which returns ``{"reply": ...}`` or ``{"error": ...}``.
- This module must import standalone (no dependency on app.py).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from bogi.config import settings
from bogi.db import get_session
from bogi.models import (
    Capture,
    Document,
    Habit,
    Memory,
    Monitor,
    Person,
    Transaction,
)
from bogi.modules import (
    brief,
    calendars,
    capture,
    habits,
    money,
    monitors,
    people,
    tool_permissions,
)
from bogi.tz import now_local


def _now_iso() -> str:
    return datetime.now().isoformat()


def _user_id() -> int | None:
    """First allowed user, or None if none configured."""
    ids = settings.allowed_user_ids
    return ids[0] if ids else None


_NO_USER = {"error": "no user configured"}


# --------------------------------------------------------------------------- #
# Handlers                                                                     #
# --------------------------------------------------------------------------- #


async def chat(request: Request) -> JSONResponse:
    """POST /api/chat  body {"text": str} -> {"reply": str, "ts": iso}."""
    uid = _user_id()
    if uid is None:
        return JSONResponse(_NO_USER)
    try:
        body = await request.json()
        text = (body or {}).get("text", "")
        if not isinstance(text, str) or not text.strip():
            return JSONResponse({"error": "empty text"})
        reply = await request.app.state.agent.run(
            text, user_id=uid, channel="web"
        )
        return JSONResponse({"reply": reply, "ts": _now_iso()})
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


async def status(request: Request) -> JSONResponse:
    """GET /api/status -> ok, tools, counts{...}, ts."""
    uid = _user_id()
    if uid is None:
        return JSONResponse(_NO_USER)
    try:
        models = {
            "memories": Memory,
            "people": Person,
            "captures": Capture,
            "transactions": Transaction,
            "monitors": Monitor,
            "habits": Habit,
            "documents": Document,
        }
        counts: dict[str, int] = {}
        async with get_session() as session:
            for key, model in models.items():
                result = await session.execute(
                    select(func.count()).select_from(model)
                )
                counts[key] = int(result.scalar_one())
        return JSONResponse(
            {
                "ok": True,
                "tools": len(tool_permissions.REGISTRY),
                "counts": counts,
                "ts": _now_iso(),
            }
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


async def today(request: Request) -> JSONResponse:
    """GET /api/today -> agenda[], deadlines[], agenda_error|null."""
    uid = _user_id()
    if uid is None:
        return JSONResponse(_NO_USER)
    try:
        day = now_local().strftime("%Y-%m-%d")

        agenda: list[dict] = []
        agenda_error: str | None = None
        try:
            agenda = await calendars.agenda(date_from=day, date_to=day)
        except Exception as exc:
            agenda_error = str(exc)
            agenda = []

        # Deadlines need a live Playwright/Moodle session — isolate them so a
        # browser/login/network hiccup degrades to an empty list instead of
        # nuking the whole panel (agenda included).
        deadlines: list[dict] = []
        deadlines_error: str | None = None
        try:
            deadlines = await request.app.state.agent.fmi.get_upcoming_events()
        except Exception as exc:
            deadlines_error = str(exc)
            deadlines = []

        return JSONResponse(
            {
                "agenda": agenda,
                "deadlines": deadlines,
                "agenda_error": agenda_error,
                "deadlines_error": deadlines_error,
            }
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


async def money_view(request: Request) -> JSONResponse:
    """GET /api/money -> {"month": <monthly_summary>, "recent": <recent>}."""
    uid = _user_id()
    if uid is None:
        return JSONResponse(_NO_USER)
    try:
        month = await money.monthly_summary(uid)
        recent = await money.recent(uid, limit=10)
        return JSONResponse({"month": month, "recent": recent})
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


async def people_view(request: Request) -> JSONResponse:
    """GET /api/people -> <due_followups> {"stale":[...],"birthdays":[...]}."""
    uid = _user_id()
    if uid is None:
        return JSONResponse(_NO_USER)
    try:
        return JSONResponse(await people.due_followups(uid))
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


async def habits_view(request: Request) -> JSONResponse:
    """GET /api/habits -> {"habits": <status>}."""
    uid = _user_id()
    if uid is None:
        return JSONResponse(_NO_USER)
    try:
        return JSONResponse({"habits": await habits.status(uid)})
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


async def captures_view(request: Request) -> JSONResponse:
    """GET /api/captures -> {"inbox": <inbox(limit=20)>}."""
    uid = _user_id()
    if uid is None:
        return JSONResponse(_NO_USER)
    try:
        return JSONResponse({"inbox": await capture.inbox(uid, limit=20)})
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


async def monitors_view(request: Request) -> JSONResponse:
    """GET /api/monitors -> {"monitors": <list_monitors>}."""
    uid = _user_id()
    if uid is None:
        return JSONResponse(_NO_USER)
    try:
        return JSONResponse({"monitors": await monitors.list_monitors(uid)})
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


async def brief_view(request: Request) -> JSONResponse:
    """GET /api/brief -> {"text": <compose_brief(uid, agent.fmi)>}."""
    uid = _user_id()
    if uid is None:
        return JSONResponse(_NO_USER)
    try:
        text = await brief.compose_brief(uid, request.app.state.agent.fmi)
        return JSONResponse({"text": text})
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


async def healthz(request: Request) -> JSONResponse:
    """GET /healthz -> {"ok": true}. No auth (allowlisted by AuthMiddleware)."""
    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------- #
# Route table                                                                 #
# --------------------------------------------------------------------------- #


def api_routes() -> list[Route]:
    """Starlette routes for CONTRACT 3. Consumed by the lead's app.py."""
    return [
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/status", status, methods=["GET"]),
        Route("/api/today", today, methods=["GET"]),
        Route("/api/money", money_view, methods=["GET"]),
        Route("/api/people", people_view, methods=["GET"]),
        Route("/api/habits", habits_view, methods=["GET"]),
        Route("/api/captures", captures_view, methods=["GET"]),
        Route("/api/monitors", monitors_view, methods=["GET"]),
        Route("/api/brief", brief_view, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
    ]
