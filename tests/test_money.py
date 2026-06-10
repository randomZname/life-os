"""Money module tests.

DB-touching: each test uses a sentinel (BIG random) user_id and hard-deletes
that user's transactions in a finally block so production data stays clean.

Coverage:
- log_transaction: income + expense, returns positive ids.
- invalid kind raises ValueError; non-positive amount raises ValueError.
- report: income/expense totals, net, count, currency, by_category breakdown.
- monthly_summary: wraps report for the current local month + adds labels.
- recent: newest occurred_at first.
"""

from __future__ import annotations

import random

import pytest

from bogi.modules import money
from bogi.tz import now_local


def _fake_user() -> int:
    return 9_000_000_000 + random.randint(1, 10_000_000)


async def _cleanup(user_id: int) -> None:
    from sqlalchemy import delete

    from bogi.db import get_session
    from bogi.models import Transaction

    async with get_session() as session:
        await session.execute(
            delete(Transaction).where(Transaction.user_id == user_id)
        )


# -------- validation (still touches nothing before raising) ------------------


@pytest.mark.asyncio
async def test_invalid_kind_raises():
    uid = _fake_user()
    try:
        with pytest.raises(ValueError):
            await money.log_transaction(uid, "transfer", 10.0)
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_nonpositive_amount_raises():
    uid = _fake_user()
    try:
        with pytest.raises(ValueError):
            await money.log_transaction(uid, "income", 0)
        with pytest.raises(ValueError):
            await money.log_transaction(uid, "expense", -5)
    finally:
        await _cleanup(uid)


# -------- log + report -------------------------------------------------------


@pytest.mark.asyncio
async def test_log_and_report_totals():
    uid = _fake_user()
    try:
        i1 = await money.log_transaction(
            uid, "income", 30.0, category="tutoring", description="урок мат",
            occurred_at="2026-06-01",
        )
        i2 = await money.log_transaction(
            uid, "income", 20.0, category="tutoring", occurred_at="2026-06-02",
        )
        e1 = await money.log_transaction(
            uid, "expense", 12.5, category="food", occurred_at="2026-06-02",
        )
        assert all(isinstance(x, int) and x > 0 for x in (i1, i2, e1))

        rep = await money.report(uid)
        assert rep["income_total"] == 50.0
        assert rep["expense_total"] == 12.5
        assert rep["net"] == 37.5
        assert rep["count"] == 3
        assert rep["currency"] == "BGN"
        assert rep["by_category"]["tutoring"]["income"] == 50.0
        assert rep["by_category"]["food"]["expense"] == 12.5
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_report_date_window_filters():
    uid = _fake_user()
    try:
        await money.log_transaction(uid, "income", 100.0, occurred_at="2026-01-15")
        await money.log_transaction(uid, "income", 40.0, occurred_at="2026-06-15")

        rep = await money.report(uid, date_from="2026-06-01", date_to="2026-06-30")
        assert rep["income_total"] == 40.0
        assert rep["count"] == 1
    finally:
        await _cleanup(uid)


# -------- monthly_summary ----------------------------------------------------


@pytest.mark.asyncio
async def test_monthly_summary_current_month():
    uid = _fake_user()
    today = now_local()
    try:
        # Log a transaction dated today (current local month).
        await money.log_transaction(
            uid, "income", 25.0, category="tutoring",
            occurred_at=today.strftime("%Y-%m-%d"),
        )
        summary = await money.monthly_summary(uid)
        assert summary["year"] == today.year
        assert summary["month"] == today.month
        assert summary["label"] == f"{today.year:04d}-{today.month:02d}"
        assert summary["income_total"] == 25.0
        assert summary["count"] == 1
    finally:
        await _cleanup(uid)


@pytest.mark.asyncio
async def test_monthly_summary_explicit_month():
    uid = _fake_user()
    try:
        await money.log_transaction(uid, "expense", 9.0, occurred_at="2026-03-10")
        summary = await money.monthly_summary(uid, year=2026, month=3)
        assert summary["label"] == "2026-03"
        assert summary["expense_total"] == 9.0
        assert summary["net"] == -9.0
    finally:
        await _cleanup(uid)


# -------- recent -------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_orders_newest_first():
    uid = _fake_user()
    try:
        await money.log_transaction(uid, "income", 10.0, occurred_at="2026-01-01")
        await money.log_transaction(uid, "income", 20.0, occurred_at="2026-03-01")
        await money.log_transaction(uid, "income", 30.0, occurred_at="2026-02-01")

        items = await money.recent(uid, limit=10)
        assert len(items) == 3
        dates = [it["occurred_at"] for it in items]
        assert dates == sorted(dates, reverse=True)
        # newest (March) first
        assert items[0]["amount"] == 20.0
        # required keys present
        for it in items:
            assert set(it) >= {
                "id", "kind", "amount", "currency", "category",
                "description", "occurred_at", "person_id",
            }

        # limit honored
        limited = await money.recent(uid, limit=2)
        assert len(limited) == 2
    finally:
        await _cleanup(uid)
