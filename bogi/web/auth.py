"""Authentication for the BogiAgent web dashboard (Phase 5).

Security-sensitive. Uses only the Python stdlib + Starlette:

- ``hash_password`` / ``verify_password`` — pbkdf2_sha256 with a per-password
  random salt and a constant-time compare.
- ``RateLimiter`` — in-memory per-IP failed-login lockout.
- ``AuthMiddleware`` — enforces a valid session on every path except an
  explicit allowlist (``/login``, ``/logout``, ``/static``, ``/healthz``).
- ``routes`` — the ``GET``/``POST`` ``/login`` + ``/logout`` Starlette routes,
  rendering a self-contained themed ``login.html`` (no external assets).

The module is framework-light: it depends on Starlette (the web layer is allowed
to) but never on the agent/LLM frameworks. Settings are read lazily from
``bogi.config.settings`` so tests can monkeypatch them.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from bogi.config import settings

# pbkdf2 parameters. Iterations are deliberately high (security checklist:
# >= 200k). Stored format: "pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>".
_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000
_SALT_BYTES = 16


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #
def hash_password(pw: str) -> str:
    """Hash a password with pbkdf2_sha256 and a fresh random salt.

    Returns ``"pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>"``.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    """Constant-time verify ``pw`` against a stored pbkdf2 string.

    Returns ``False`` on any parse error — never raises.
    """
    try:
        algo, iters_s, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        iterations = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False

    if not expected:
        return False

    candidate = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


# --------------------------------------------------------------------------- #
# Rate limiting / lockout (in-memory, per IP)
# --------------------------------------------------------------------------- #
class RateLimiter:
    """In-memory per-IP failed-login limiter.

    After ``settings.web_login_max_attempts`` failures inside the lockout
    window, the IP is locked until ``now + settings.web_login_lockout_minutes``.
    ``now`` is injectable so tests can simulate time without sleeping.
    """

    def __init__(self) -> None:
        # ip -> {"failures": int, "locked_until": float (unix ts) | 0.0}
        self._state: dict[str, dict[str, float]] = {}

    @staticmethod
    def _now(now: float | None) -> float:
        return time.time() if now is None else now

    def is_locked(self, ip: str, now: float | None = None) -> bool:
        entry = self._state.get(ip)
        if not entry:
            return False
        locked_until = entry.get("locked_until", 0.0)
        if locked_until <= self._now(now):
            # Lock expired — clear it so the counter resets cleanly.
            if locked_until:
                self._state.pop(ip, None)
            return False
        return True

    def record_failure(self, ip: str, now: float | None = None) -> None:
        ts = self._now(now)
        entry = self._state.get(ip)
        # If a previous lock has expired, start fresh.
        if entry is None or (entry.get("locked_until", 0.0) and entry["locked_until"] <= ts):
            entry = {"failures": 0.0, "locked_until": 0.0}
        entry["failures"] += 1
        if entry["failures"] >= settings.web_login_max_attempts:
            entry["locked_until"] = ts + settings.web_login_lockout_minutes * 60
        self._state[ip] = entry

    def record_success(self, ip: str) -> None:
        self._state.pop(ip, None)


# Module-level singleton used by the routes + middleware.
limiter = RateLimiter()


# --------------------------------------------------------------------------- #
# Request helpers
# --------------------------------------------------------------------------- #
def client_ip(request: Request) -> str:
    """Best-effort client IP.

    Behind Cloudflare the real client is the first entry of
    ``X-Forwarded-For``; otherwise fall back to the peer address.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def current_user(request: Request) -> str | None:
    """Return the logged-in username from the session, or ``None``."""
    try:
        return request.session.get("user")
    except (AssertionError, KeyError):
        # SessionMiddleware not installed / no session scope.
        return None


# --------------------------------------------------------------------------- #
# Auth middleware
# --------------------------------------------------------------------------- #
_ALLOWLIST_PREFIXES = ("/login", "/logout", "/static", "/healthz")


class AuthMiddleware(BaseHTTPMiddleware):
    """Require a valid session on every path except the allowlist.

    Unauthenticated requests to ``/api/*`` get a 401 JSON body; any other
    path is redirected to ``/login``.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if path.startswith(_ALLOWLIST_PREFIXES):
            return await call_next(request)

        # Auth globally disabled (local/dev opt-out — see web_cmd startup warning).
        if not settings.web_auth_enabled:
            return await call_next(request)

        if current_user(request) is not None:
            return await call_next(request)

        if path.startswith("/api"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse("/login", status_code=302)


# --------------------------------------------------------------------------- #
# Login page (self-contained, themed — same palette as the dashboard)
# --------------------------------------------------------------------------- #
_LOGIN_HTML = """\
<!DOCTYPE html>
<html lang="bg">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>J.A.R.V.I.S. — Достъп</title>
<style>
  :root{{
    --bg:#04070d; --cyan:#34e7ff; --cyan-dim:#0e5d6e; --magenta:#ff3cac;
    --text:#cfeefb; --muted:#5d7a8c;
    --grid:rgba(52,231,255,.07);
    --panel:rgba(8,18,30,.78); --line:rgba(52,231,255,.18);
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  html,body{{height:100%}}
  body{{
    font-family:'Segoe UI',Rajdhani,system-ui,sans-serif;
    background:radial-gradient(1200px 700px at 70% -10%,#0a1c2e 0%,var(--bg) 60%),var(--bg);
    color:var(--text);min-height:100vh;display:grid;place-items:center;overflow:hidden;
  }}
  .bg-grid{{position:fixed;inset:0;z-index:0;pointer-events:none;
    background-image:linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
    background-size:46px 46px;
    mask-image:radial-gradient(ellipse 80% 70% at 50% 40%,#000 40%,transparent 100%)}}
  .glow{{position:fixed;border-radius:50%;filter:blur(90px);opacity:.4;pointer-events:none;z-index:0}}
  .glow.c{{width:380px;height:380px;background:var(--cyan);top:-120px;right:-80px}}
  .glow.m{{width:320px;height:320px;background:var(--magenta);bottom:-140px;left:-100px;opacity:.28}}
  .card{{position:relative;z-index:10;width:340px;max-width:92vw;
    border:1px solid var(--line);border-radius:16px;background:var(--panel);
    backdrop-filter:blur(8px);padding:30px 28px;
    box-shadow:0 0 40px rgba(52,231,255,.1),inset 0 0 30px rgba(52,231,255,.04)}}
  .mark{{display:flex;justify-content:center;margin-bottom:14px}}
  .mark svg{{filter:drop-shadow(0 0 8px var(--cyan));animation:spin 14s linear infinite}}
  @keyframes spin{{to{{transform:rotate(360deg)}}}}
  h1{{text-align:center;font-size:18px;letter-spacing:5px;font-weight:700;margin-bottom:4px;
    background:linear-gradient(90deg,var(--cyan),#fff 60%,var(--magenta));
    -webkit-background-clip:text;background-clip:text;color:transparent}}
  .sub{{text-align:center;font-size:9px;letter-spacing:3px;color:var(--muted);margin-bottom:22px}}
  label{{display:block;font-size:10px;letter-spacing:2px;color:var(--muted);margin:14px 0 6px}}
  input{{width:100%;background:rgba(0,0,0,.35);border:1px solid var(--line);border-radius:10px;
    padding:12px 14px;color:var(--text);font-size:14px;outline:none;font-family:inherit}}
  input:focus{{border-color:var(--cyan);box-shadow:0 0 0 2px rgba(52,231,255,.12)}}
  button{{width:100%;margin-top:22px;border:none;cursor:pointer;border-radius:10px;padding:13px;
    font-weight:700;letter-spacing:3px;font-size:12px;color:#04111a;
    background:linear-gradient(135deg,var(--cyan),#7af0ff);
    box-shadow:0 0 20px rgba(52,231,255,.4);transition:.15s}}
  button:hover{{transform:translateY(-1px);box-shadow:0 0 30px rgba(52,231,255,.7)}}
  .err{{margin-top:16px;font-size:12px;letter-spacing:.5px;text-align:center;
    color:#ff7ab0;border:1px solid rgba(255,60,172,.3);border-radius:10px;
    padding:10px 12px;background:rgba(255,60,172,.07)}}
  .err:empty{{display:none}}
  .foot{{text-align:center;margin-top:18px;font-size:9px;letter-spacing:2px;color:var(--muted)}}
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="glow c"></div>
<div class="glow m"></div>
<form class="card" method="post" action="/login" autocomplete="off">
  <div class="mark">
    <svg width="48" height="48" viewBox="0 0 38 38" fill="none">
      <circle cx="19" cy="19" r="17" stroke="var(--cyan)" stroke-width="1.4" opacity=".5"/>
      <circle cx="19" cy="19" r="11" stroke="var(--magenta)" stroke-width="1" stroke-dasharray="3 3" opacity=".7"/>
      <circle cx="19" cy="19" r="5" fill="var(--cyan)"/>
    </svg>
  </div>
  <h1>J.A.R.V.I.S.</h1>
  <div class="sub">SECURE ACCESS</div>
  <label for="username">ПОТРЕБИТЕЛ</label>
  <input id="username" name="username" type="text" autofocus required>
  <label for="password">ПАРОЛА</label>
  <input id="password" name="password" type="password" required>
  <button type="submit">ВХОД</button>
  <div class="err">{error}</div>
  <div class="foot">{footer}</div>
</form>
</body>
</html>
"""

_DEFAULT_FOOTER = "127.0.0.1 · CLOUDFLARE ACCESS"
_LOCKED_FOOTER = "ВРЕМЕННО ЗАКЛЮЧЕНО · ОПИТАЙ ПО-КЪСНО"


def _esc(text: str) -> str:
    """Minimal HTML-escape for the (controlled) error/footer strings."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_login(error: str = "", locked: bool = False, status_code: int = 200) -> HTMLResponse:
    """Render the self-contained themed login page."""
    footer = _LOCKED_FOOTER if locked else _DEFAULT_FOOTER
    html = _LOGIN_HTML.format(error=_esc(error), footer=_esc(footer))
    return HTMLResponse(html, status_code=status_code)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
async def login_get(request: Request) -> Response:
    ip = client_ip(request)
    error = request.query_params.get("error", "")
    locked = limiter.is_locked(ip)
    if locked and not error:
        error = "Прекалено много опити. Опитай отново след малко."
    return render_login(error=error, locked=locked)


async def login_post(request: Request) -> Response:
    ip = client_ip(request)

    if limiter.is_locked(ip):
        return render_login(
            error="Прекалено много опити. Опитай отново след малко.",
            locked=True,
            status_code=429,
        )

    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))

    cfg_user = settings.web_username
    cfg_hash = settings.web_password_hash
    if not cfg_user or not cfg_hash:
        return render_login(
            error="Достъпът не е конфигуриран — пусни `bogi web-auth`.",
            status_code=200,
        )

    # Constant-time check of BOTH username and password. Compute both halves
    # unconditionally (no early-out) so a wrong username is indistinguishable
    # from a wrong password timing-wise.
    user_ok = hmac.compare_digest(username.encode("utf-8"), cfg_user.encode("utf-8"))
    pw_ok = verify_password(password, cfg_hash)

    if user_ok and pw_ok:
        limiter.record_success(ip)
        request.session["user"] = username
        return RedirectResponse("/", status_code=302)

    # Failure: count it, add a small fixed delay, re-render.
    limiter.record_failure(ip)
    await asyncio.sleep(0.5)
    locked = limiter.is_locked(ip)
    return render_login(error="Грешен потребител или парола.", locked=locked, status_code=200)


async def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


routes: list[Route] = [
    Route("/login", login_get, methods=["GET"]),
    Route("/login", login_post, methods=["POST"]),
    Route("/logout", logout, methods=["GET", "POST"]),
]
