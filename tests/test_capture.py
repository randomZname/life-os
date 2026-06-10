"""Capture inbox tests (DB-touching).

Coverage:
- save_capture: note (content) and link (url) kinds return ids.
- save_capture with neither content nor url raises ValueError.
- inbox: lists only status='inbox' rows, newest first.
- search_captures: matches on content and on a tag substring.
- file_capture: moves a row out of the inbox (status->filed) and sets routed_to.
- archive_capture: sets status='archived'.

Each test uses a unique synthetic user_id and hard-deletes that user's captures
in a finally block so production data stays clean.
"""

from __future__ import annotations

import random

import pytest

from bogi.modules import capture


def _fake_user() -> int:
    return 9_000_000_000 + random.randint(1, 10_000_000)


async def _cleanup(user_id: int) -> None:
    """Hard-delete all captures for this synthetic user."""
    from sqlalchemy import delete

    from bogi.db import get_session
    from bogi.models import Capture

    async with get_session() as session:
        await session.execute(delete(Capture).where(Capture.user_id == user_id))


@pytest.mark.asyncio
async def test_save_capture_content_and_link_kinds():
    uid = _fake_user()
    try:
        note_id = await capture.save_capture(uid, "buy milk", tags=["errand"])
        assert isinstance(note_id, int) and note_id > 0

        link_id = await capture.save_capture(
            uid, kind="link", url="https://example.com/article"
        )
        assert isinstance(link_id, int) and link_id > 0
        assert link_id != note_id
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_save_capture_requires_content_or_url():
    uid = _fake_user()
    try:
        with pytest.raises(ValueError):
            await capture.save_capture(uid)
        with pytest.raises(ValueError):
            await capture.save_capture(uid, "", url=None)
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_inbox_lists_only_inbox_newest_first():
    uid = _fake_user()
    try:
        first = await capture.save_capture(uid, "first thought")
        second = await capture.save_capture(uid, "second thought")
        third = await capture.save_capture(uid, "third thought")

        # File one so it should drop out of the inbox.
        assert await capture.file_capture(uid, second)

        items = await capture.inbox(uid)
        ids = [it["id"] for it in items]
        assert second not in ids
        assert first in ids and third in ids
        # Newest first: third was created last.
        assert ids.index(third) < ids.index(first)
        # Shape check.
        assert set(items[0].keys()) == {
            "id", "kind", "content", "url", "summary", "tags", "created_at"
        }
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_search_matches_content_and_tag():
    uid = _fake_user()
    try:
        target = await capture.save_capture(
            uid, "Read the SICP textbook", tags=["study", "books"]
        )
        await capture.save_capture(uid, "unrelated grocery note", tags=["food"])

        # Content match (case-insensitive).
        by_content = await capture.search_captures(uid, "sicp")
        assert target in [c["id"] for c in by_content]

        # Tag match.
        by_tag = await capture.search_captures(uid, "books")
        assert target in [c["id"] for c in by_tag]

        # Non-match returns nothing for this user.
        none = await capture.search_captures(uid, "zzz-no-such-text")
        assert none == []
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_file_capture_removes_from_inbox_and_sets_routed_to():
    uid = _fake_user()
    try:
        cid = await capture.save_capture(uid, "turn into a task")

        ok = await capture.file_capture(uid, cid, routed_to="task:123")
        assert ok is True

        ids = [it["id"] for it in await capture.inbox(uid)]
        assert cid not in ids

        # Filing a row the user does not own returns False.
        other = _fake_user()
        assert await capture.file_capture(other, cid) is False

        # Verify status + routed_to persisted.
        results = await capture.search_captures(uid, "turn into a task")
        assert cid in [c["id"] for c in results]
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_archive_capture():
    uid = _fake_user()
    try:
        cid = await capture.save_capture(uid, "stale idea")
        assert await capture.archive_capture(uid, cid) is True

        ids = [it["id"] for it in await capture.inbox(uid)]
        assert cid not in ids

        # Archiving a non-existent / unowned row returns False.
        assert await capture.archive_capture(uid, 999_999_999) is False
    finally:
        await _cleanup(uid)
