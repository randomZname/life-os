"""Approval queue tests (V2 §2.B).

DB-touching tests use synthetic user_ids (BIG random) and hard-delete their
rows afterwards so production approvals stay clean.
"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest

from bogi.modules import approvals


def _fake_user() -> int:
    return 9_100_000_000 + random.randint(1, 10_000_000)


async def _cleanup(user_id: int) -> None:
    from sqlalchemy import delete
    from bogi.db import get_session
    from bogi.models import Approval
    async with get_session() as session:
        await session.execute(delete(Approval).where(Approval.user_id == user_id))


# -------- pure function --------------------------------------------------------

def test_request_key_is_stable_and_order_independent():
    k1 = approvals.make_request_key(1, "send_email", {"to": "a@b.bg", "body": "hi"})
    k2 = approvals.make_request_key(1, "send_email", {"body": "hi", "to": "a@b.bg"})
    assert k1 == k2  # key order must not matter
    k3 = approvals.make_request_key(1, "send_email", {"to": "x@y.bg", "body": "hi"})
    assert k1 != k3


@pytest.mark.asyncio
async def test_resolve_rejects_bad_decision():
    with pytest.raises(ValueError):
        await approvals.resolve(1, "maybe", 1)


# -------- DB-touching ----------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_get_pending():
    uid = _fake_user()
    try:
        aid = await approvals.create(uid, "demo_tool", {"x": 1}, "do x=1")
        row = await approvals.get(aid)
        assert row is not None
        assert row["status"] == approvals.PENDING
        assert row["payload"] == {"x": 1}
        assert row["tool_name"] == "demo_tool"
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_create_dedups_identical_pending():
    uid = _fake_user()
    try:
        a1 = await approvals.create(uid, "demo_tool", {"x": 1}, "do x=1")
        a2 = await approvals.create(uid, "demo_tool", {"x": 1}, "do x=1")
        assert a1 == a2, "identical pending request should reuse the row"
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_approve_then_double_resolve_is_idempotent():
    uid = _fake_user()
    try:
        aid = await approvals.create(uid, "demo_tool", {"x": 1}, "do x=1")
        r1 = await approvals.resolve(aid, approvals.APPROVED, decided_by=uid)
        assert r1["status"] == approvals.APPROVED
        assert r1["decided_by"] == uid
        # second resolve (e.g. double button click) must not flip/raise
        r2 = await approvals.resolve(aid, approvals.REJECTED, decided_by=uid)
        assert r2["status"] == approvals.APPROVED  # unchanged
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_find_decided_returns_match():
    uid = _fake_user()
    try:
        aid = await approvals.create(uid, "demo_tool", {"x": 7}, "do x=7")
        await approvals.resolve(aid, approvals.APPROVED, decided_by=uid)
        found = await approvals.find_decided(uid, "demo_tool", {"x": 7})
        assert found is not None and found["status"] == approvals.APPROVED
        # different payload -> no decided match
        none = await approvals.find_decided(uid, "demo_tool", {"x": 999})
        assert none is None
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_expired_pending_is_not_actionable():
    uid = _fake_user()
    try:
        # negative TTL -> already expired on creation
        aid = await approvals.create(uid, "demo_tool", {"x": 1}, "do x=1", ttl=timedelta(seconds=-1))
        row = await approvals.get(aid)
        assert row["status"] == approvals.EXPIRED
        # resolving an expired row does not approve it
        r = await approvals.resolve(aid, approvals.APPROVED, decided_by=uid)
        assert r["status"] == approvals.EXPIRED
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_list_pending_and_expire_stale():
    uid = _fake_user()
    try:
        await approvals.create(uid, "demo_tool", {"x": 1}, "p1")
        await approvals.create(uid, "demo_tool", {"x": 2}, "p2")
        pending = await approvals.list_pending(uid)
        assert len(pending) == 2
        n = await approvals.expire_stale()  # nothing overdue -> 0 for this user's rows
        assert n >= 0
    finally:
        await _cleanup(uid)
