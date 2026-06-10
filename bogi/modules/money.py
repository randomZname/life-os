"""Money / tutoring finance: log income & expenses, report, summarise.

Богдан дава частни уроци (income) и има разходи (expense). Този модул е тънък
слой над таблицата ``Transaction`` — записва транзакции и смята отчети.

Framework-agnostic: NO pydantic_ai / litellm imports. Чете през ``bogi.db``.

Time model: DB-то пази ``occurred_at`` като naive UTC (както ``datetime.utcnow``).
Когато ползвателят даде 'YYYY-MM-DD', интерпретираме го като LOCAL ден
(Europe/Sofia) и го превръщаме в naive-UTC граници за сравнение в SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from bogi.db import get_session
from bogi.models import Transaction
from bogi.tz import local_tz, now_local

VALID_KINDS = ("income", "expense")


# --- time helpers ------------------------------------------------------------


def _local_day_bounds_utc(date_str: str, *, end: bool = False) -> datetime:
    """Parse 'YYYY-MM-DD' as a LOCAL day boundary → naive UTC datetime.

    Mirrors ``calendars._parse_day`` but returns a *naive* UTC datetime, because
    ``Transaction.occurred_at`` is stored naive-UTC. ``end=True`` snaps to the
    end of that local day (23:59:59.999999).
    """
    dt = datetime.fromisoformat((date_str or "").strip())
    if end:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    aware = dt.replace(tzinfo=local_tz()).astimezone(UTC)
    return aware.replace(tzinfo=None)


def _parse_occurred_at(value: str | None) -> datetime:
    """Parse an ISO occurred_at into naive UTC, or now (UTC) if None.

    Accepts bare dates ('YYYY-MM-DD' → start of that LOCAL day) and full ISO
    timestamps. Tz-aware inputs are converted to UTC; naive inputs are assumed
    to already be UTC.
    """
    if value is None:
        return datetime.now(UTC).replace(tzinfo=None)
    v = value.strip().replace(" ", "T")
    if "T" not in v:
        # Bare date → start of that local day.
        return _local_day_bounds_utc(v)
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


# --- write -------------------------------------------------------------------


async def log_transaction(
    user_id: int,
    kind: str,
    amount: float,
    *,
    currency: str = "BGN",
    category: str | None = None,
    description: str | None = None,
    person_id: int | None = None,
    occurred_at: str | None = None,
    source: str = "manual",
) -> int:
    """Record one transaction and return its id.

    ``kind`` must be 'income' or 'expense'. ``amount`` must be a positive number.
    ``occurred_at`` is an ISO string (or 'YYYY-MM-DD', local day) or None→now.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}, got {kind!r}")
    try:
        amount = float(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"amount must be a number, got {amount!r}") from exc
    if amount <= 0:
        raise ValueError(f"amount must be > 0, got {amount}")

    when = _parse_occurred_at(occurred_at)

    async with get_session() as session:
        tx = Transaction(
            user_id=user_id,
            kind=kind,
            amount=amount,
            currency=currency,
            category=category,
            description=description,
            person_id=person_id,
            occurred_at=when,
            source=source,
        )
        session.add(tx)
        await session.flush()
        return tx.id


# --- read / report -----------------------------------------------------------


async def report(
    user_id: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Aggregate transactions for a user over an (optional) date window.

    ``date_from`` / ``date_to`` are 'YYYY-MM-DD' (inclusive LOCAL day bounds) or
    None (=all time). Returns income/expense totals, net, count, currency, and a
    per-category income/expense breakdown. Money is rounded to 2 decimals.
    """
    stmt = select(Transaction).where(Transaction.user_id == user_id)
    if date_from:
        stmt = stmt.where(Transaction.occurred_at >= _local_day_bounds_utc(date_from))
    if date_to:
        stmt = stmt.where(
            Transaction.occurred_at <= _local_day_bounds_utc(date_to, end=True)
        )

    async with get_session() as session:
        rows = (await session.execute(stmt)).scalars().all()

    income_total = 0.0
    expense_total = 0.0
    by_category: dict[str, dict[str, float]] = {}
    currency: str | None = None

    for tx in rows:
        if currency is None:
            currency = tx.currency
        cat = tx.category or "uncategorized"
        bucket = by_category.setdefault(cat, {"income": 0.0, "expense": 0.0})
        if tx.kind == "income":
            income_total += tx.amount
            bucket["income"] += tx.amount
        elif tx.kind == "expense":
            expense_total += tx.amount
            bucket["expense"] += tx.amount

    for bucket in by_category.values():
        bucket["income"] = round(bucket["income"], 2)
        bucket["expense"] = round(bucket["expense"], 2)

    income_total = round(income_total, 2)
    expense_total = round(expense_total, 2)
    return {
        "income_total": income_total,
        "expense_total": expense_total,
        "net": round(income_total - expense_total, 2),
        "currency": currency or "BGN",
        "count": len(rows),
        "by_category": by_category,
    }


async def monthly_summary(
    user_id: int,
    *,
    year: int | None = None,
    month: int | None = None,
) -> dict[str, Any]:
    """Report for one calendar month (defaults to the current LOCAL month).

    Returns everything ``report`` returns plus {year, month, label} where label
    is 'YYYY-MM'.
    """
    today = now_local()
    year = year if year is not None else today.year
    month = month if month is not None else today.month

    first = datetime(year, month, 1)
    if month == 12:
        next_first = datetime(year + 1, 1, 1)
    else:
        next_first = datetime(year, month + 1, 1)
    last = next_first - timedelta(days=1)

    data = await report(
        user_id,
        date_from=first.strftime("%Y-%m-%d"),
        date_to=last.strftime("%Y-%m-%d"),
    )
    data["year"] = year
    data["month"] = month
    data["label"] = f"{year:04d}-{month:02d}"
    return data


async def recent(user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    """Most recent transactions (newest ``occurred_at`` first)."""
    stmt = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(limit)
    )
    async with get_session() as session:
        rows = (await session.execute(stmt)).scalars().all()

    return [
        {
            "id": tx.id,
            "kind": tx.kind,
            "amount": round(tx.amount, 2),
            "currency": tx.currency,
            "category": tx.category,
            "description": tx.description,
            "occurred_at": tx.occurred_at.isoformat() if tx.occurred_at else None,
            "person_id": tx.person_id,
        }
        for tx in rows
    ]
