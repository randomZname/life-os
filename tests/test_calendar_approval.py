"""Calendar approved-writer tests (PHASE 2, option A: update-only).

Covers the new surface:
- `bogi/modules/approval_exec.py` registry + dispatch (Google API mocked).
- `bogi/modules/approvals.py` `resolve(...)` `_just_decided` single-execution guard.

Google Calendar is always mocked — no real API call. `gcal.update_event` is
looked up at call time inside `approval_exec`, so monkeypatching the attribute
on the `gcal` module is enough.

DB-touching tests use synthetic user_ids (BIG random) and hard-delete their
rows afterwards so production approvals stay clean (matches test_approvals.py).
"""

from __future__ import annotations

import random

from bogi.modules import approval_exec, approvals, gcal


def _fake_user() -> int:
    return 9_100_000_000 + random.randint(1, 10_000_000)


async def _cleanup(user_id: int) -> None:
    from sqlalchemy import delete

    from bogi.db import get_session
    from bogi.models import Approval
    async with get_session() as session:
        await session.execute(delete(Approval).where(Approval.user_id == user_id))


def _fake_update_event(calls: list[dict]):
    """Build a fake async gcal.update_event that records its kwargs and returns
    a realistic `_normalize_event`-shaped dict."""
    async def fake(**payload):
        calls.append(dict(payload))
        return {
            "id": payload["event_id"],
            "summary": payload.get("summary", ""),
            "start": payload.get("start", ""),
        }
    return fake


# -------- registry -------------------------------------------------------------

def test_registry_has_executor():
    assert approval_exec.has_executor("calendar.update_event") is True
    assert approval_exec.has_executor("calendar.delete_event") is True
    assert approval_exec.has_executor("nope") is False


async def test_run_unknown_tool_raises_keyerror():
    import pytest
    with pytest.raises(KeyError):
        await approval_exec.run("nope", {})


# -------- executor dispatch (mocked gcal) --------------------------------------

async def test_executor_dispatch_calls_gcal_once(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(gcal, "update_event", _fake_update_event(calls))

    out = await approval_exec.run(
        "calendar.update_event", {"event_id": "E1", "summary": "New"}
    )

    assert len(calls) == 1
    assert calls[0] == {"event_id": "E1", "summary": "New"}
    assert out  # non-empty string
    assert "New" in out


async def test_delete_executor_dispatch_calls_gcal_once(monkeypatch):
    calls: list[dict] = []

    async def fake_delete(**payload):
        calls.append(dict(payload))
        return {"id": payload["event_id"], "deleted": True}

    monkeypatch.setattr(gcal, "delete_event", fake_delete)

    out = await approval_exec.run("calendar.delete_event", {"event_id": "D1"})

    assert len(calls) == 1
    assert calls[0] == {"event_id": "D1"}
    assert "D1" in out


# -------- _just_decided single-execution guard (real DB) -----------------------

async def test_resolve_just_decided_only_on_transition():
    uid = _fake_user()
    payload = {"event_id": "E2", "summary": "Updated title"}
    try:
        aid = await approvals.create(
            uid, "calendar.update_event", payload, "update E2"
        )
        r1 = await approvals.resolve(aid, approvals.APPROVED, decided_by=uid)
        assert r1["status"] == approvals.APPROVED
        assert r1["_just_decided"] is True
        # idempotent re-call: still approved, but no longer a fresh transition
        r2 = await approvals.resolve(aid, approvals.APPROVED, decided_by=uid)
        assert r2["status"] == approvals.APPROVED
        assert r2["_just_decided"] is False
    finally:
        await _cleanup(uid)


# -------- end-to-end guard wiring (real DB + mocked gcal) ----------------------

async def test_double_resolve_executes_gcal_exactly_once(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(gcal, "update_event", _fake_update_event(calls))

    uid = _fake_user()
    payload = {"event_id": "E3", "summary": "Once only"}
    try:
        aid = await approvals.create(
            uid, "calendar.update_event", payload, "update E3"
        )
        # Drive the exact callback rule twice.
        for _ in range(2):
            r = await approvals.resolve(aid, approvals.APPROVED, decided_by=uid)
            if r["status"] == approvals.APPROVED and r["_just_decided"]:
                await approval_exec.run(r["tool_name"], r["payload"])

        assert len(calls) == 1, "executor must fire only on the deciding resolve"
        assert calls[0] == {"event_id": "E3", "summary": "Once only"}
    finally:
        await _cleanup(uid)
