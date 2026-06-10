"""Approval queue — gate for agent-initiated external writes (V2 §2.B).

Framework-agnostic: no pydantic_ai / litellm imports. The agent layer
(`bogi/agent.py`) and the Telegram layer (`bogi/telegram_bot.py`) build on top
of these functions.

State machine:  pending --approve--> approved
                pending --reject--> rejected
                pending --(>expires_at)--> expired

`resolve()` is idempotent: deciding an already-decided row is a no-op that
returns the existing row, so a double-click on a Telegram button is safe.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from bogi.db import get_session
from bogi.models import Approval

# How long a pending approval stays actionable before it auto-expires.
DEFAULT_TTL = timedelta(hours=24)

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"


def make_request_key(user_id: int, tool_name: str, payload: dict[str, Any]) -> str:
    """Stable hash of an action, for idempotent dedup of identical requests."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    raw = f"{user_id}|{tool_name}|{blob}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _row_to_dict(a: Approval) -> dict[str, Any]:
    return {
        "id": a.id,
        "user_id": a.user_id,
        "tool_name": a.tool_name,
        "payload": json.loads(a.payload),
        "preview": a.preview,
        "status": a.status,
        "request_key": a.request_key,
        "decided_by": a.decided_by,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
    }


async def create(
    user_id: int,
    tool_name: str,
    payload: dict[str, Any],
    preview: str,
    ttl: timedelta = DEFAULT_TTL,
) -> int:
    """Record a pending approval, returning its id.

    If an identical pending request already exists (same request_key, still
    pending and not expired), reuse it instead of creating a duplicate.
    """
    key = make_request_key(user_id, tool_name, payload)
    now = datetime.utcnow()
    async with get_session() as session:
        existing = await session.execute(
            select(Approval).where(
                Approval.request_key == key,
                Approval.status == PENDING,
                Approval.expires_at > now,
            )
        )
        row = existing.scalars().first()
        if row is not None:
            return row.id

        approval = Approval(
            user_id=user_id,
            tool_name=tool_name,
            payload=json.dumps(payload, ensure_ascii=False, default=str),
            preview=preview,
            status=PENDING,
            request_key=key,
            expires_at=now + ttl,
        )
        session.add(approval)
        await session.flush()
        return approval.id


async def get(approval_id: int) -> dict[str, Any] | None:
    """Fetch one approval as a dict, expiring it lazily if its TTL passed."""
    async with get_session() as session:
        a = await session.get(Approval, approval_id)
        if a is None:
            return None
        if a.status == PENDING and a.expires_at <= datetime.utcnow():
            a.status = EXPIRED
            a.decided_at = datetime.utcnow()
        return _row_to_dict(a)


async def find_decided(
    user_id: int, tool_name: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Return the latest non-pending approval matching this exact action, if any.

    Used by the agent gate: if a matching row is already approved/rejected, act
    on it instead of asking again.
    """
    key = make_request_key(user_id, tool_name, payload)
    async with get_session() as session:
        result = await session.execute(
            select(Approval)
            .where(Approval.request_key == key, Approval.status != PENDING)
            .order_by(Approval.id.desc())
        )
        row = result.scalars().first()
        return _row_to_dict(row) if row else None


async def resolve(approval_id: int, decision: str, decided_by: int) -> dict[str, Any] | None:
    """Approve or reject a pending approval. Idempotent.

    `decision` must be 'approved' or 'rejected'. Resolving an already-decided
    row returns it unchanged (safe double-click). Returns None if not found.
    """
    if decision not in (APPROVED, REJECTED):
        raise ValueError(f"decision must be {APPROVED!r} or {REJECTED!r}, got {decision!r}")
    async with get_session() as session:
        a = await session.get(Approval, approval_id)
        if a is None:
            return None
        if a.status != PENDING:
            out = _row_to_dict(a)  # idempotent no-op
            out["_just_decided"] = False
            return out
        if a.expires_at <= datetime.utcnow():
            a.status = EXPIRED
            a.decided_at = datetime.utcnow()
            out = _row_to_dict(a)
            out["_just_decided"] = False
            return out
        a.status = decision
        a.decided_by = decided_by
        a.decided_at = datetime.utcnow()
        out = _row_to_dict(a)
        out["_just_decided"] = True
        return out


async def list_pending(user_id: int) -> list[dict[str, Any]]:
    """All still-actionable pending approvals for a user (expiring stale ones)."""
    now = datetime.utcnow()
    async with get_session() as session:
        result = await session.execute(
            select(Approval)
            .where(Approval.user_id == user_id, Approval.status == PENDING)
            .order_by(Approval.id.desc())
        )
        rows = list(result.scalars().all())
        out = []
        for a in rows:
            if a.expires_at <= now:
                a.status = EXPIRED
                a.decided_at = now
                continue
            out.append(_row_to_dict(a))
        return out


async def expire_stale() -> int:
    """Mark all overdue pending approvals as expired. Returns count affected."""
    now = datetime.utcnow()
    async with get_session() as session:
        result = await session.execute(
            update(Approval)
            .where(Approval.status == PENDING, Approval.expires_at <= now)
            .values(status=EXPIRED, decided_at=now)
        )
        return result.rowcount or 0
