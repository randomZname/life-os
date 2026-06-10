"""Gmail client — READ (list / search / read message) + SEND.

OAuth flow on first run:
    `python -m bogi gmail-auth` отваря браузър → ти оторизираш → token cache в
    `data/gcal/gmail_token.json`. Refresh става автоматично от lib-а след това.

Uses a **separate token file** and its **own scopes** (`gmail.readonly` +
`gmail.send`) so it never invalidates the existing Google Calendar token
(`data/gcal/token.json`). The OAuth client secret (`settings.gcal_client_secret`)
is shared. ⚠️ Bumping the scope (e.g. adding `gmail.send`) requires re-running
`python -m bogi gmail-auth` **once** — the cached readonly token is locked to the
old scopes and must be re-consented. It is still a separate token from Calendar.

Framework-agnostic — никакъв pydantic_ai import тук. Agent registration е в
`bogi/agent.py`.

Surface: list_recent / search / read_message (READ) + send_message (SEND).
**No modify, no labels.**

Lethal Trifecta анализ:
  - Private data: да (твоят inbox)
  - Untrusted content: ДА — message bodies/snippets са attacker-controllable
    (всеки може да ти прати mail). Тези функции връщат UNTRUSTED съдържание;
    lead-ът ги wrap-ва с `wrap_untrusted` на agent слоя.
  - External comm: ДА — `gmail.send` scope-ът е вече ОТВОРЕН.
  ⇒ trifecta-та е технически отворена и в трите leg-а. MITIGATED, защото
  `send_message` се вика **само от approval executor**-а след user ✅. Agent
  tool-ът само enqueue-ва approval — нищо не се праща без потвърждение.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from bogi.config import settings

logger = logging.getLogger(__name__)

# READ + SEND. Separate token file from Calendar so the two never clobber each
# other. Bumping this requires re-running OAuth — cached token is locked to old
# scopes. The send leg re-opens the Lethal Trifecta but is mitigated because only
# the approval executor ever calls send_message (see module doc).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# Own token path — sibling of the calendar token, NOT the same file.
GMAIL_TOKEN: Path = settings.gcal_token.parent / "gmail_token.json"

# Don't ship megabyte-long bodies into the agent context.
_MAX_BODY_CHARS = 20_000


def _load_credentials() -> Credentials:
    """Read cached token; refresh if expired; raise if not authorized yet."""
    token_path: Path = GMAIL_TOKEN
    secret_path: Path = settings.gcal_client_secret

    if not secret_path.exists():
        raise FileNotFoundError(
            f"client_secret.json not found at {secret_path}. "
            "Download OAuth client from Google Cloud Console (Desktop type) "
            "and save it there."
        )

    if not token_path.exists():
        raise RuntimeError(
            "Gmail not authorized yet. Run `python -m bogi gmail-auth` "
            "to authorize (opens browser)."
        )

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
            logger.info("Gmail token refreshed")
        else:
            raise RuntimeError(
                "Gmail token is invalid and cannot refresh. "
                "Re-run `python -m bogi gmail-auth`."
            )
    return creds


def authorize_interactive() -> Path:
    """Run OAuth flow: opens local browser; stores token in `data/gcal/gmail_token.json`.

    Safe to re-run — overwrites the Gmail token only (never the calendar token).
    """
    secret_path: Path = settings.gcal_client_secret
    token_path: Path = GMAIL_TOKEN
    if not secret_path.exists():
        raise FileNotFoundError(
            f"client_secret.json not found at {secret_path}. "
            "Download from Google Cloud Console first."
        )

    token_path.parent.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    logger.info("Gmail authorized; token saved to %s", token_path)
    return token_path


def _build_service():
    """Construct Gmail API service (sync — wrap with asyncio.to_thread)."""
    creds = _load_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _headers_map(payload: dict[str, Any]) -> dict[str, str]:
    """Lower-cased header name → value, from a message payload."""
    out: dict[str, str] = {}
    for h in payload.get("headers", []) or []:
        name = (h.get("name") or "").lower()
        if name:
            out[name] = h.get("value", "") or ""
    return out


def _summarize_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Strip a Gmail message (metadata level) to the fields the agent needs."""
    payload = msg.get("payload", {}) or {}
    headers = _headers_map(payload)
    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": msg.get("snippet", "") or "",
    }


def _decode_b64url(data: str) -> str:
    """Decode Gmail's base64url body data to text (lenient on padding)."""
    if not data:
        return ""
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    return raw.decode("utf-8", errors="replace")


def _extract_plain_body(payload: dict[str, Any]) -> str:
    """Walk the MIME tree and return the first text/plain body found.

    Prefers text/plain; recurses into multipart containers. Returns "" if no
    plain part exists (caller falls back to the snippet).
    """
    mime = payload.get("mimeType", "") or ""
    body = payload.get("body", {}) or {}

    if mime == "text/plain" and body.get("data"):
        return _decode_b64url(body["data"])

    for part in payload.get("parts", []) or []:
        found = _extract_plain_body(part)
        if found:
            return found

    return ""


def _detail_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Full single-message view: headers + plain-text body (snippet fallback)."""
    payload = msg.get("payload", {}) or {}
    headers = _headers_map(payload)
    snippet = msg.get("snippet", "") or ""

    body = _extract_plain_body(payload) or snippet
    if len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS]

    return {
        "id": msg.get("id", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "body": body,
        "snippet": snippet,
    }


def _list_summaries_sync(
    *, query: str | None, max_results: int
) -> list[dict[str, Any]]:
    """List message ids (optionally filtered), then fetch metadata for each."""
    service = _build_service()
    try:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        out: list[dict[str, Any]] = []
        for ref in resp.get("messages", []) or []:
            msg = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=ref["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            out.append(_summarize_message(msg))
    except HttpError as exc:
        logger.exception("Gmail API error (list)")
        raise RuntimeError(f"Gmail API error: {exc}") from exc

    return out


async def list_recent(max_results: int = 10) -> list[dict[str, Any]]:
    """Most recent inbox messages (metadata).

    Returns a list of dicts: {id, thread_id, from, subject, date, snippet}.
    UNTRUSTED content — wrapped by the agent layer.
    """
    return await asyncio.to_thread(
        _list_summaries_sync, query=None, max_results=max_results
    )


async def search(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Search messages using Gmail query syntax (e.g. `from:fmi is:unread`).

    Returns a list of dicts: {id, thread_id, from, subject, date, snippet}.
    UNTRUSTED content — wrapped by the agent layer.
    """
    return await asyncio.to_thread(
        _list_summaries_sync, query=query, max_results=max_results
    )


def _read_message_sync(message_id: str) -> dict[str, Any]:
    service = _build_service()
    try:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
    except HttpError as exc:
        logger.exception("Gmail API error (read)")
        raise RuntimeError(f"Gmail API error: {exc}") from exc
    return _detail_message(msg)


async def read_message(message_id: str) -> dict[str, Any]:
    """Fetch one full message: {id, from, to, subject, date, body, snippet}.

    `body` is the plain-text part (base64url-decoded, capped at 20k chars),
    falling back to the snippet when no text/plain part exists.
    UNTRUSTED content — wrapped by the agent layer.
    """
    return await asyncio.to_thread(_read_message_sync, message_id)


def _send_message_sync(to: str, subject: str, body: str) -> dict[str, Any]:
    service = _build_service()
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    try:
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
    except HttpError as exc:
        logger.exception("Gmail API error (send)")
        raise RuntimeError(f"Gmail API error: {exc}") from exc
    return {
        "id": sent.get("id", ""),
        "thread_id": sent.get("threadId", ""),
        "to": to,
        "subject": subject,
    }


async def send_message(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send a plain-text email (UTF-8) as the authenticated user.

    Returns {"id", "thread_id", "to", "subject"}. Raises RuntimeError on API error.
    SEND is approval-gated at the agent layer — this is only ever invoked by the
    approval executor after the user approves.
    """
    return await asyncio.to_thread(_send_message_sync, to, subject, body)
