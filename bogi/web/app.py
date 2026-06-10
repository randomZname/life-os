"""Assemble the Starlette web app: session + auth + API + dashboard.

Layering: SignedCookieSessionMiddleware (outer, populates request.session) →
AuthMiddleware (inner, enforces login) → routes. The dashboard at "/" and all
"/api/*" require a valid session; only /login, /logout, /healthz are open.
Modules stay framework-agnostic; this file is the HTTP adapter (peer to
telegram_bot.py).
"""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Route

from bogi.config import settings
from bogi.web import api, auth
from bogi.web.session import SignedCookieSessionMiddleware

_STATIC = Path(__file__).parent / "static"


async def _dashboard(request):
    return FileResponse(_STATIC / "dashboard.html")


def build_app() -> Starlette:
    routes = [
        Route("/", _dashboard),
        *auth.routes,
        *api.api_routes(),
    ]
    app = Starlette(routes=routes)
    # add_middleware: last added = outermost. Session must wrap (run before) auth.
    app.add_middleware(auth.AuthMiddleware)
    app.add_middleware(
        SignedCookieSessionMiddleware,
        secret=settings.web_session_secret,
        https_only=settings.web_secure_cookie,
        same_site="lax",
    )

    # Shared agent (constructing is cheap — no network at build time).
    from bogi.agent import BogiAgent

    app.state.agent = BogiAgent()
    return app
