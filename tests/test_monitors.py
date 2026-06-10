"""Monitors module tests.

Coverage:
- current_value (PURE, no DB): keyword present/absent; signature changes when
  text changes; whitespace normalization.
- add/list/remove (DB-touching): create, list shape, soft-delete semantics.
- check_monitor (DB-touching, FAKE async fetcher — NO network):
    * first run (last_value is None) -> changed False (baseline), per the
      module's documented first-run semantics; baseline persisted.
    * second run, SAME text -> changed False.
    * third run, DIFFERENT text -> changed True; new value persisted.

DB-touching tests use a sentinel user_id (BIG random) and hard-delete that
user's monitors in a finally block so production data stays clean.
"""

from __future__ import annotations

import random

import pytest

from bogi.modules import monitors

# -------- current_value (pure function, no DB) -------------------------------


def test_current_value_keyword_present():
    text = "Header line\nPrice: 42 BGN\nFooter"
    assert monitors.current_value(text, "price") == "Price: 42 BGN"


def test_current_value_keyword_case_insensitive():
    text = "Some INTRO\nIn Stock now\nbye"
    assert monitors.current_value(text, "in stock") == "In Stock now"


def test_current_value_keyword_absent_returns_empty():
    text = "nothing\nrelevant\nhere"
    assert monitors.current_value(text, "price") == ""


def test_current_value_signature_format_and_stability():
    text = "alpha\nbeta\ngamma"
    sig = monitors.current_value(text, None)
    assert ":" in sig
    length, _, digest = sig.partition(":")
    assert length.isdigit()
    assert len(digest) == 16
    # Stable: same input -> same signature.
    assert monitors.current_value(text, None) == sig


def test_current_value_signature_changes_with_text():
    a = monitors.current_value("hello world", None)
    b = monitors.current_value("hello there", None)
    assert a != b


def test_current_value_signature_ignores_whitespace_noise():
    # Trailing spaces / extra blank lines normalize away -> same signature.
    a = monitors.current_value("one\ntwo", None)
    b = monitors.current_value("  one  \n\n\n  two  \n", None)
    assert a == b


def test_current_value_empty_rule_uses_signature():
    # Empty / whitespace rule falls back to signature, not keyword search.
    sig = monitors.current_value("foo\nbar", "   ")
    assert ":" in sig


# -------- DB-touching tests --------------------------------------------------


def _fake_user() -> int:
    return 9_000_000_000 + random.randint(1, 10_000_000)


async def _cleanup(user_id: int) -> None:
    from sqlalchemy import delete

    from bogi.db import get_session
    from bogi.models import Monitor

    async with get_session() as session:
        await session.execute(delete(Monitor).where(Monitor.user_id == user_id))


@pytest.mark.asyncio
async def test_add_list_remove():
    uid = _fake_user()
    try:
        mid = await monitors.add_monitor(
            uid, "FMI news", "https://fmi.uni-sofia.bg/news", rule="контролно"
        )
        assert isinstance(mid, int) and mid > 0

        items = await monitors.list_monitors(uid)
        assert len(items) == 1
        item = items[0]
        assert item["id"] == mid
        assert item["name"] == "FMI news"
        assert item["kind"] == "webpage"
        assert item["target_url"] == "https://fmi.uni-sofia.bg/news"
        assert item["rule"] == "контролно"
        assert item["last_value"] is None
        assert item["last_checked_at"] is None
        assert item["active"] is True

        # Soft-delete.
        ok = await monitors.remove_monitor(uid, mid)
        assert ok is True
        assert await monitors.list_monitors(uid, active_only=True) == []
        # Still present when including inactive.
        all_items = await monitors.list_monitors(uid, active_only=False)
        assert len(all_items) == 1
        assert all_items[0]["active"] is False

        # Removing again / unknown id -> False.
        assert await monitors.remove_monitor(uid, 999_999_999) is False
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_check_monitor_baseline_then_change():
    uid = _fake_user()
    try:
        mid = await monitors.add_monitor(
            uid, "page", "https://example.com/x", rule=None
        )

        # Controllable fake fetcher — NO network.
        state = {"text": "version one\nstable line"}

        async def fake_fetch(url: str) -> str:
            return state["text"]

        def _monitor_dict(last_value):
            return {
                "id": mid,
                "name": "page",
                "target_url": "https://example.com/x",
                "rule": None,
                "last_value": last_value,
            }

        # First run: last_value None -> baseline, changed False (documented).
        r1 = await monitors.check_monitor(_monitor_dict(None), fake_fetch)
        assert r1["monitor_id"] == mid
        assert r1["name"] == "page"
        assert r1["changed"] is False
        assert r1["old"] is None
        baseline = r1["new"]
        assert baseline

        # Baseline persisted to DB.
        persisted = (await monitors.list_monitors(uid))[0]
        assert persisted["last_value"] == baseline
        assert persisted["last_checked_at"] is not None

        # Second run, SAME text -> changed False.
        r2 = await monitors.check_monitor(_monitor_dict(baseline), fake_fetch)
        assert r2["changed"] is False
        assert r2["new"] == baseline

        # Third run, DIFFERENT text -> changed True, new value persisted.
        state["text"] = "version two\ncompletely different"
        r3 = await monitors.check_monitor(_monitor_dict(baseline), fake_fetch)
        assert r3["changed"] is True
        assert r3["old"] == baseline
        assert r3["new"] != baseline

        persisted_after = (await monitors.list_monitors(uid))[0]
        assert persisted_after["last_value"] == r3["new"]
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_check_monitor_fetch_error_does_not_raise():
    uid = _fake_user()
    try:
        mid = await monitors.add_monitor(uid, "broken", "https://example.com/y")

        async def failing_fetch(url: str) -> str:
            raise RuntimeError("boom")

        monitor = {
            "id": mid,
            "name": "broken",
            "target_url": "https://example.com/y",
            "rule": None,
            "last_value": None,
        }
        result = await monitors.check_monitor(monitor, failing_fetch)
        assert result["monitor_id"] == mid
        assert result["changed"] is False
        assert "boom" in result["error"]
    finally:
        await _cleanup(uid)
