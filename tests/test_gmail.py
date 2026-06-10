"""Gmail read-only client tests — no network, no credentials.

We monkeypatch `gmail._build_service` with a fake service that mimics the
chained Gmail API surface: `.users().messages().list/get(...).execute()`.
Asserts header→field mapping, base64url body decoding, snippet fallback,
and HttpError → RuntimeError.
"""

from __future__ import annotations

import base64

import pytest
from googleapiclient.errors import HttpError

from bogi.modules import gmail


def _b64url(text: str) -> str:
    """Encode like Gmail does (base64url, padding may be stripped)."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


class _FakeExecute:
    """Wraps a result (or an exception to raise) behind `.execute()`."""

    def __init__(self, result=None, raises: Exception | None = None):
        self._result = result
        self._raises = raises

    def execute(self):
        if self._raises is not None:
            raise self._raises
        return self._result


class _FakeMessages:
    def __init__(self, list_result, get_results, raises=None):
        self._list_result = list_result
        self._get_results = get_results  # id -> message dict
        self._raises = raises

    def list(self, **kwargs):
        return _FakeExecute(self._list_result, raises=self._raises)

    def get(self, *, userId, id, **kwargs):  # noqa: N803, A002 (Google API kwarg names)
        return _FakeExecute(self._get_results.get(id), raises=self._raises)


class _FakeUsers:
    def __init__(self, messages: _FakeMessages):
        self._messages = messages

    def messages(self):
        return self._messages


class _FakeService:
    def __init__(self, messages: _FakeMessages):
        self._users = _FakeUsers(messages)

    def users(self):
        return self._users


def _http_error() -> HttpError:
    class _Resp:
        status = 403
        reason = "Forbidden"

    return HttpError(_Resp(), b'{"error": "denied"}')


def _msg(msg_id, *, headers, snippet="", thread_id="t1", payload_extra=None):
    payload = {"headers": [{"name": k, "value": v} for k, v in headers.items()]}
    if payload_extra:
        payload.update(payload_extra)
    return {"id": msg_id, "threadId": thread_id, "snippet": snippet, "payload": payload}


def _install(monkeypatch, messages: _FakeMessages):
    monkeypatch.setattr(gmail, "_build_service", lambda: _FakeService(messages))


# --- token path + scope are the safe, separate ones --------------------------


def test_scopes_include_readonly_and_send():
    assert "https://www.googleapis.com/auth/gmail.readonly" in gmail.SCOPES
    assert "https://www.googleapis.com/auth/gmail.send" in gmail.SCOPES


def test_token_path_is_separate_from_calendar():
    from bogi.config import settings

    assert gmail.GMAIL_TOKEN.name == "gmail_token.json"
    assert gmail.GMAIL_TOKEN != settings.gcal_token
    assert gmail.GMAIL_TOKEN.parent == settings.gcal_token.parent


# --- list_recent / search header mapping -------------------------------------


async def test_list_recent_maps_headers_and_snippet(monkeypatch):
    get_results = {
        "m1": _msg(
            "m1",
            headers={"From": "Alice <a@x.bg>", "Subject": "Здравей", "Date": "Mon, 1 Jun 2026"},
            snippet="preview text",
            thread_id="th9",
        ),
    }
    messages = _FakeMessages({"messages": [{"id": "m1"}]}, get_results)
    _install(monkeypatch, messages)

    out = await gmail.list_recent(max_results=5)
    assert len(out) == 1
    row = out[0]
    assert row["id"] == "m1"
    assert row["thread_id"] == "th9"
    assert row["from"] == "Alice <a@x.bg>"
    assert row["subject"] == "Здравей"
    assert row["date"] == "Mon, 1 Jun 2026"
    assert row["snippet"] == "preview text"


async def test_search_maps_headers(monkeypatch):
    get_results = {
        "m2": _msg("m2", headers={"From": "bob@y.com", "Subject": "Report"}, snippet="s"),
    }
    messages = _FakeMessages({"messages": [{"id": "m2"}]}, get_results)
    _install(monkeypatch, messages)

    out = await gmail.search("from:bob", max_results=3)
    assert out[0]["from"] == "bob@y.com"
    assert out[0]["subject"] == "Report"
    assert out[0]["snippet"] == "s"


async def test_list_recent_empty_inbox(monkeypatch):
    messages = _FakeMessages({}, {})  # no "messages" key
    _install(monkeypatch, messages)
    assert await gmail.list_recent() == []


# --- read_message: base64url body decode + fallback --------------------------


async def test_read_message_decodes_plain_text_body(monkeypatch):
    body_text = "Това е тялото на писмото.\nLine two."
    msg = _msg(
        "m3",
        headers={
            "From": "sender@x.bg",
            "To": "me@x.bg",
            "Subject": "Subj",
            "Date": "Tue, 2 Jun 2026",
        },
        snippet="snip",
        payload_extra={
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64url("<p>ignored</p>")}},
                {"mimeType": "text/plain", "body": {"data": _b64url(body_text)}},
            ],
        },
    )
    messages = _FakeMessages({}, {"m3": msg})
    _install(monkeypatch, messages)

    out = await gmail.read_message("m3")
    assert out["id"] == "m3"
    assert out["from"] == "sender@x.bg"
    assert out["to"] == "me@x.bg"
    assert out["subject"] == "Subj"
    assert out["date"] == "Tue, 2 Jun 2026"
    assert out["body"] == body_text
    assert out["snippet"] == "snip"


async def test_read_message_falls_back_to_snippet(monkeypatch):
    msg = _msg(
        "m4",
        headers={"From": "x@x.bg", "Subject": "no body"},
        snippet="just a snippet",
        payload_extra={"mimeType": "text/html", "body": {"data": _b64url("<b>hi</b>")}},
    )
    messages = _FakeMessages({}, {"m4": msg})
    _install(monkeypatch, messages)

    out = await gmail.read_message("m4")
    assert out["body"] == "just a snippet"


async def test_read_message_caps_body_length(monkeypatch):
    big = "x" * 30_000
    msg = _msg(
        "m5",
        headers={"From": "x@x.bg"},
        payload_extra={"mimeType": "text/plain", "body": {"data": _b64url(big)}},
    )
    messages = _FakeMessages({}, {"m5": msg})
    _install(monkeypatch, messages)

    out = await gmail.read_message("m5")
    assert len(out["body"]) == gmail._MAX_BODY_CHARS


# --- HttpError → RuntimeError ------------------------------------------------


async def test_list_http_error_becomes_runtime_error(monkeypatch):
    messages = _FakeMessages(None, {}, raises=_http_error())
    _install(monkeypatch, messages)
    with pytest.raises(RuntimeError, match="Gmail API error"):
        await gmail.list_recent()


async def test_read_http_error_becomes_runtime_error(monkeypatch):
    messages = _FakeMessages(None, {}, raises=_http_error())
    _install(monkeypatch, messages)
    with pytest.raises(RuntimeError, match="Gmail API error"):
        await gmail.read_message("whatever")
