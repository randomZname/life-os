"""Tests for the web dashboard auth module (Phase 5, CONTRACT 2).

Covers password hashing/verification, the in-memory rate limiter (with an
injected clock — no real sleeping), and the ``AuthMiddleware`` + login routes
via a tiny Starlette app.

To avoid adding a dependency (Starlette's ``SessionMiddleware`` requires the
third-party ``itsdangerous``), these tests install a minimal in-memory
session middleware that provides the same ``request.session`` dict contract
the auth module relies on. The production session backend is the lead's
concern (app.py).
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from bogi.config import settings
from bogi.web import auth
from bogi.web.auth import (
    AuthMiddleware,
    RateLimiter,
    hash_password,
    verify_password,
)


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #
def test_hash_verify_roundtrip():
    stored = hash_password("correct horse battery staple")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("correct horse battery staple", stored) is True


def test_verify_wrong_password():
    stored = hash_password("s3cret")
    assert verify_password("wrong", stored) is False


def test_hash_format_and_iterations():
    stored = hash_password("pw")
    algo, iters, salt_hex, hash_hex = stored.split("$")
    assert algo == "pbkdf2_sha256"
    assert int(iters) >= 200_000
    assert len(bytes.fromhex(salt_hex)) == 16
    assert len(hash_hex) > 0


def test_verify_tampered_stored_returns_false():
    stored = hash_password("pw")
    # Garbage / malformed strings must never raise, only return False.
    assert verify_password("pw", "not-a-valid-hash") is False
    assert verify_password("pw", "") is False
    assert verify_password("pw", "pbkdf2_sha256$200000$deadbeef") is False
    assert verify_password("pw", stored + "00") is False  # flipped hash bytes
    assert verify_password("pw", stored.replace("pbkdf2_sha256", "md5")) is False


def test_two_hashes_of_same_password_differ():
    a = hash_password("same")
    b = hash_password("same")
    assert a != b  # random salt
    assert verify_password("same", a)
    assert verify_password("same", b)


# --------------------------------------------------------------------------- #
# RateLimiter (injected clock — no sleeping)
# --------------------------------------------------------------------------- #
def test_rate_limiter_locks_and_clears(monkeypatch):
    monkeypatch.setattr(settings, "web_login_max_attempts", 3)
    monkeypatch.setattr(settings, "web_login_lockout_minutes", 15)
    rl = RateLimiter()
    ip = "1.2.3.4"
    t = 1000.0

    assert rl.is_locked(ip, now=t) is False
    rl.record_failure(ip, now=t)
    rl.record_failure(ip, now=t)
    assert rl.is_locked(ip, now=t) is False  # 2 < 3
    rl.record_failure(ip, now=t)
    assert rl.is_locked(ip, now=t) is True  # hit the limit

    # record_success clears the IP.
    rl.record_success(ip)
    assert rl.is_locked(ip, now=t) is False


def test_rate_limiter_lock_expires(monkeypatch):
    monkeypatch.setattr(settings, "web_login_max_attempts", 2)
    monkeypatch.setattr(settings, "web_login_lockout_minutes", 15)
    rl = RateLimiter()
    ip = "9.9.9.9"
    t = 5000.0

    rl.record_failure(ip, now=t)
    rl.record_failure(ip, now=t)
    assert rl.is_locked(ip, now=t) is True

    # Still locked just before the window ends.
    assert rl.is_locked(ip, now=t + 15 * 60 - 1) is True
    # Unlocked once the window passes.
    assert rl.is_locked(ip, now=t + 15 * 60 + 1) is False
    # And the counter reset — a single failure must not re-lock.
    rl.record_failure(ip, now=t + 15 * 60 + 1)
    assert rl.is_locked(ip, now=t + 15 * 60 + 1) is False


def test_rate_limiter_per_ip_isolation(monkeypatch):
    monkeypatch.setattr(settings, "web_login_max_attempts", 1)
    monkeypatch.setattr(settings, "web_login_lockout_minutes", 5)
    rl = RateLimiter()
    rl.record_failure("a", now=0.0)
    assert rl.is_locked("a", now=0.0) is True
    assert rl.is_locked("b", now=0.0) is False


# --------------------------------------------------------------------------- #
# AuthMiddleware + routes (tiny Starlette app)
# --------------------------------------------------------------------------- #
class _MemorySessionMiddleware:
    """Minimal in-memory session backend providing ``request.session``.

    Stdlib-only stand-in for Starlette's SessionMiddleware (which needs the
    third-party ``itsdangerous``). The session dict is keyed by a cookie the
    TestClient persists, so it survives across requests like the real one.
    """

    _store: ClassVar[dict[str, dict]] = {}
    _counter: ClassVar[list[int]] = [0]
    COOKIE: ClassVar[str] = "memsession"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from starlette.requests import Request

        request = Request(scope, receive=receive)
        sid = request.cookies.get(self.COOKIE)
        if not sid or sid not in self._store:
            sid = None
        session = dict(self._store.get(sid, {})) if sid else {}
        scope["session"] = session
        set_cookie = {"done": False}

        async def send_wrapper(message):
            if message["type"] == "http.response.start" and not set_cookie["done"]:
                set_cookie["done"] = True
                nonlocal sid
                # Persist whatever the handler left in scope["session"].
                cur = scope["session"]
                if cur:
                    if not sid:
                        self._counter[0] += 1
                        sid = f"sid{self._counter[0]}"
                    self._store[sid] = dict(cur)
                    headers = message.setdefault("headers", [])
                    headers.append(
                        (b"set-cookie", f"{self.COOKIE}={sid}; Path=/".encode())
                    )
                elif sid:
                    # Session cleared (logout) — drop it.
                    self._store.pop(sid, None)
            await send(message)

        await self.app(scope, receive, send_wrapper)


async def _protected(request):
    return PlainTextResponse(f"hello {request.session.get('user')}")


async def _api_x(request):
    return JSONResponse({"ok": True})


def _make_app() -> Starlette:
    app_routes = [
        Route("/", _protected, methods=["GET"]),
        Route("/api/x", _api_x, methods=["GET"]),
        *auth.routes,
    ]
    app = Starlette(routes=app_routes)
    # Order matters: session (outer) -> auth (inner). Add auth first then
    # session so session wraps auth (Starlette applies the last-added outermost).
    app.add_middleware(AuthMiddleware)
    app.add_middleware(_MemorySessionMiddleware)
    return app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "web_username", "u")
    monkeypatch.setattr(settings, "web_password_hash", hash_password("pw"))
    # Force auth ON for the gating tests regardless of the local .env
    # (a dev machine may set WEB_AUTH_ENABLED=false).
    monkeypatch.setattr(settings, "web_auth_enabled", True)
    monkeypatch.setattr(settings, "web_login_max_attempts", 5)
    monkeypatch.setattr(settings, "web_login_lockout_minutes", 15)
    # Fresh limiter so prior tests don't leak lock state.
    monkeypatch.setattr(auth, "limiter", RateLimiter())
    _MemorySessionMiddleware._store.clear()
    return TestClient(_make_app())


def test_unauth_html_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_unauth_api_returns_401(client):
    r = client.get("/api/x")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "J.A.R.V.I.S." in r.text
    assert 'name="password"' in r.text


def test_login_wrong_password_rerenders_not_authed(client):
    r = client.post(
        "/login",
        data={"username": "u", "password": "nope"},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Грешен" in r.text
    # Still not authed.
    assert client.get("/", follow_redirects=False).status_code == 302


def test_login_success_sets_session_and_grants_access(client):
    r = client.post(
        "/login",
        data={"username": "u", "password": "pw"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    # Session persisted via cookie — protected route now works.
    r2 = client.get("/", follow_redirects=False)
    assert r2.status_code == 200
    assert "hello u" in r2.text


def test_login_not_configured_message(monkeypatch):
    monkeypatch.setattr(settings, "web_username", "")
    monkeypatch.setattr(settings, "web_password_hash", "")
    monkeypatch.setattr(auth, "limiter", RateLimiter())
    _MemorySessionMiddleware._store.clear()
    c = TestClient(_make_app())
    r = c.post("/login", data={"username": "x", "password": "y"})
    assert r.status_code == 200
    assert "web-auth" in r.text


def test_lockout_returns_429(monkeypatch):
    monkeypatch.setattr(settings, "web_username", "u")
    monkeypatch.setattr(settings, "web_password_hash", hash_password("pw"))
    monkeypatch.setattr(settings, "web_login_max_attempts", 2)
    monkeypatch.setattr(settings, "web_login_lockout_minutes", 15)
    rl = RateLimiter()
    rl.record_failure("testclient")
    rl.record_failure("testclient")
    monkeypatch.setattr(auth, "limiter", rl)
    _MemorySessionMiddleware._store.clear()
    c = TestClient(_make_app())
    r = c.post(
        "/login",
        data={"username": "u", "password": "pw"},
        follow_redirects=False,
    )
    assert r.status_code == 429


def test_logout_clears_session(client):
    client.post("/login", data={"username": "u", "password": "pw"}, follow_redirects=False)
    assert client.get("/", follow_redirects=False).status_code == 200
    r = client.get("/logout", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 302


def test_allowlisted_paths_open_when_unauth(client):
    # /login is reachable without a session.
    assert client.get("/login").status_code == 200


def test_auth_disabled_grants_access_without_session(monkeypatch, client):
    # WEB_AUTH_ENABLED=false bypasses the gate entirely (local/dev opt-out).
    monkeypatch.setattr(settings, "web_auth_enabled", False)
    assert client.get("/", follow_redirects=False).status_code == 200
    assert client.get("/api/x").status_code == 200


def test_auth_enabled_by_default_still_gates(client):
    # Default (web_auth_enabled=True): unauth still blocked — secure by default.
    assert client.get("/", follow_redirects=False).status_code == 302
    assert client.get("/api/x").status_code == 401


def test_client_ip_prefers_xforwarded(monkeypatch):
    monkeypatch.setattr(settings, "web_username", "u")
    monkeypatch.setattr(settings, "web_password_hash", hash_password("pw"))
    monkeypatch.setattr(settings, "web_login_max_attempts", 1)
    monkeypatch.setattr(settings, "web_login_lockout_minutes", 15)
    rl = RateLimiter()
    monkeypatch.setattr(auth, "limiter", rl)
    _MemorySessionMiddleware._store.clear()
    c = TestClient(_make_app())
    # One failed login from a forwarded IP locks THAT ip, not the peer.
    c.post(
        "/login",
        data={"username": "u", "password": "bad"},
        headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"},
        follow_redirects=False,
    )
    assert rl.is_locked("203.0.113.7") is True
    assert rl.is_locked("testclient") is False
