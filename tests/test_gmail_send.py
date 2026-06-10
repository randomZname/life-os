"""Gmail SEND tests — no network, no credentials.

Mirrors `tests/test_gmail.py`: monkeypatch `gmail._build_service` with a fake
service that mimics the chained Gmail API surface
`.users().messages().send(userId=..., body=...).execute()`. Covers the
send_message happy path (raw MIME round-trip), HttpError → RuntimeError, the
approval-executor wiring for `gmail.send`, and the `gmail_send` permission entry.
"""

from __future__ import annotations

import base64

import pytest
from googleapiclient.errors import HttpError

from bogi.modules import approval_exec, gmail, tool_permissions
from bogi.modules.tool_permissions import PermissionClass


def _http_error() -> HttpError:
    class _Resp:
        status = 403
        reason = "Forbidden"

    return HttpError(_Resp(), b'{"error": "denied"}')


class _FakeSend:
    """Captures the `body` it was called with; `.execute()` returns/raises."""

    def __init__(self, result=None, raises: Exception | None = None):
        self._result = result
        self._raises = raises
        self.captured_body = None

    def __call__(self, *, userId, body):  # noqa: N803 (Google API kwarg names)
        self.captured_body = body
        return self

    def execute(self):
        if self._raises is not None:
            raise self._raises
        return self._result


class _FakeMessages:
    def __init__(self, send: _FakeSend):
        self._send = send

    def send(self, **kwargs):
        return self._send(**kwargs)


class _FakeUsers:
    def __init__(self, messages: _FakeMessages):
        self._messages = messages

    def messages(self):
        return self._messages


class _FakeService:
    def __init__(self, send: _FakeSend):
        self._users = _FakeUsers(_FakeMessages(send))

    def users(self):
        return self._users


def _install(monkeypatch, send: _FakeSend):
    monkeypatch.setattr(gmail, "_build_service", lambda: _FakeService(send))


# --- send_message happy path -------------------------------------------------


async def test_send_message_happy_path(monkeypatch):
    send = _FakeSend(result={"id": "sent1", "threadId": "th1"})
    _install(monkeypatch, send)

    out = await gmail.send_message("a@b.bg", "Hi", "Body")
    assert out == {
        "id": "sent1",
        "thread_id": "th1",
        "to": "a@b.bg",
        "subject": "Hi",
    }

    # The raw MIME we handed to the API round-trips the recipient + subject.
    raw = send.captured_body["raw"]
    mime = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    assert b"a@b.bg" in mime
    assert b"Hi" in mime


# --- HttpError → RuntimeError ------------------------------------------------


async def test_send_http_error_becomes_runtime_error(monkeypatch):
    send = _FakeSend(raises=_http_error())
    _install(monkeypatch, send)
    with pytest.raises(RuntimeError, match="Gmail API error"):
        await gmail.send_message("a@b.bg", "Hi", "Body")


# --- approval executor wiring ------------------------------------------------


async def test_executor_wiring_for_gmail_send(monkeypatch):
    assert approval_exec.has_executor("gmail.send") is True

    async def _fake_send(**payload):
        return {"id": "x", "thread_id": "y", "to": payload["to"], "subject": payload["subject"]}

    monkeypatch.setattr(gmail, "send_message", _fake_send)

    out = await approval_exec.run(
        "gmail.send", {"to": "a@b.bg", "subject": "S", "body": "B"}
    )
    assert isinstance(out, str)
    assert "a@b.bg" in out
    assert "S" in out


# --- permission registry -----------------------------------------------------


def test_gmail_send_permission_is_critical_external_write():
    tp = tool_permissions.get("gmail_send")
    assert tp is not None
    assert tp.perm_class is PermissionClass.CRITICAL
    assert tp.external_write is True
    assert tp.private_data is True
    assert tool_permissions.requires_approval("gmail_send") is True
