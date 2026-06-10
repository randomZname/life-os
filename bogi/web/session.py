"""Stdlib signed-cookie session middleware — zero extra deps.

Starlette's own SessionMiddleware needs `itsdangerous`, which we don't install.
This is a small, pure-stdlib equivalent: the session dict is JSON-encoded,
base64url'd, and HMAC-SHA256 signed with `secret`, with an embedded timestamp so
we can enforce `max_age`. The signature is verified in constant time on every
request; a tampered or expired cookie yields an empty session.

Populates `scope["session"]` (so Starlette's `request.session` works) and writes
the cookie back on response when the session changed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import MutableMapping
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode(txt + pad)


class SignedCookieSessionMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        secret: str,
        cookie_name: str = "bogi_session",
        max_age: int = 14 * 24 * 3600,
        https_only: bool = True,
        same_site: str = "lax",
    ) -> None:
        self.app = app
        self.cookie_name = cookie_name
        self.max_age = max_age
        self.https_only = https_only
        self.same_site = same_site
        # Derive a stable key even if the configured secret is weak/empty.
        self._key = hashlib.sha256((secret or "bogi-insecure-dev-secret").encode()).digest()

    # --- signing -------------------------------------------------------------
    def _sign(self, payload: bytes) -> str:
        body = _b64e(payload)
        sig = hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()
        return f"{body}.{_b64e(sig)}"

    def _unsign(self, token: str) -> bytes | None:
        try:
            body, sig = token.split(".", 1)
        except ValueError:
            return None
        expected = hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()
        try:
            if not hmac.compare_digest(_b64d(sig), expected):
                return None
            return _b64d(body)
        except (ValueError, TypeError):
            return None

    def _load(self, token: str) -> dict[str, Any]:
        raw = self._unsign(token)
        if raw is None:
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if not isinstance(data, dict):
            return {}
        ts = data.pop("__ts", 0)
        if self.max_age and (time.time() - float(ts or 0)) > self.max_age:
            return {}
        return data

    def _dump(self, session: MutableMapping[str, Any]) -> str:
        data = dict(session)
        data["__ts"] = int(time.time())
        return self._sign(json.dumps(data, separators=(",", ":")).encode("utf-8"))

    # --- ASGI ----------------------------------------------------------------
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cookies = _parse_cookies(scope)
        token = cookies.get(self.cookie_name, "")
        scope["session"] = self._load(token) if token else {}
        initial = dict(scope["session"])

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                session = scope.get("session") or {}
                if dict(session) != initial:
                    headers = message.setdefault("headers", [])
                    if session:
                        cookie = self._cookie_header(self._dump(session), self.max_age)
                    else:
                        cookie = self._cookie_header("", 0)  # cleared
                    headers.append((b"set-cookie", cookie.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, send_wrapper)

    def _cookie_header(self, value: str, max_age: int) -> str:
        parts = [
            f"{self.cookie_name}={value}",
            "Path=/",
            f"Max-Age={max_age}",
            f"SameSite={self.same_site}",
            "HttpOnly",
        ]
        if self.https_only:
            parts.append("Secure")
        return "; ".join(parts)


def _parse_cookies(scope: Scope) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            for chunk in v.decode("latin-1").split(";"):
                if "=" in chunk:
                    name, _, val = chunk.strip().partition("=")
                    out[name] = val
    return out
