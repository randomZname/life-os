"""Universal capture inbox.

A quick scratchpad for thoughts, links, voice notes, photos and ideas: drop
something in now, file or recall it later. Framework-agnostic (no pydantic_ai /
litellm) — pure DB CRUD over the `Capture` table.

Public API (all async, all take user_id: int, all JSON-friendly):
    save_capture(user_id, content=None, *, kind="note", url=None, tags=None,
                 summary=None) -> int
    inbox(user_id, *, limit=50) -> list[dict]
    search_captures(user_id, query, *, limit=20) -> list[dict]
    file_capture(user_id, capture_id, *, routed_to=None, status="filed") -> bool
    archive_capture(user_id, capture_id) -> bool

Statuses: inbox | filed | archived.  Kinds: note | link | voice | photo | idea.
"""

from __future__ import annotations

from sqlalchemy import Text, cast, or_, select, update

from bogi.db import get_session
from bogi.models import Capture


def _to_dict(c: Capture) -> dict:
    """Serialize a Capture row to a JSON-friendly dict."""
    return {
        "id": c.id,
        "kind": c.kind,
        "content": c.content,
        "url": c.url,
        "summary": c.summary,
        "tags": list(c.tags) if c.tags else [],
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


async def save_capture(
    user_id: int,
    content: str | None = None,
    *,
    kind: str = "note",
    url: str | None = None,
    tags: list[str] | None = None,
    summary: str | None = None,
) -> int:
    """Save a new capture (status='inbox'). Returns the new capture id.

    At least one of `content` or `url` must be provided, else ValueError.
    """
    if not content and not url:
        raise ValueError("save_capture requires at least one of content or url")

    capture = Capture(
        user_id=user_id,
        kind=kind,
        content=content,
        url=url,
        summary=summary,
        tags=list(tags) if tags else None,
        status="inbox",
    )
    async with get_session() as session:
        session.add(capture)
        await session.flush()
        capture_id = capture.id
    return capture_id


async def inbox(user_id: int, *, limit: int = 50) -> list[dict]:
    """Return this user's unfiled captures (status='inbox'), newest first."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Capture)
                .where(Capture.user_id == user_id, Capture.status == "inbox")
                .order_by(Capture.created_at.desc(), Capture.id.desc())
                .limit(limit)
            )
        ).scalars().all()
    return [_to_dict(c) for c in rows]


async def search_captures(user_id: int, query: str, *, limit: int = 20) -> list[dict]:
    """Case-insensitive search over a user's captures, newest first.

    Matches if `query` appears in content, summary or url (ILIKE), or in any
    tag. Searches across all statuses (inbox/filed/archived).
    """
    pattern = f"%{query}%"
    # Match the query against the JSON-encoded tags array as text so a tag
    # substring counts as a hit regardless of the JSON backend.
    tags_as_text = cast(Capture.tags, Text)
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Capture)
                .where(
                    Capture.user_id == user_id,
                    or_(
                        Capture.content.ilike(pattern),
                        Capture.summary.ilike(pattern),
                        Capture.url.ilike(pattern),
                        tags_as_text.ilike(pattern),
                    ),
                )
                .order_by(Capture.created_at.desc(), Capture.id.desc())
                .limit(limit)
            )
        ).scalars().all()
    return [_to_dict(c) for c in rows]


async def file_capture(
    user_id: int,
    capture_id: int,
    *,
    routed_to: str | None = None,
    status: str = "filed",
) -> bool:
    """Mark a capture as filed (or another status) and record where it went.

    Only affects a row owned by `user_id`. Returns True if a row was updated.
    """
    async with get_session() as session:
        result = await session.execute(
            update(Capture)
            .where(Capture.id == capture_id, Capture.user_id == user_id)
            .values(status=status, routed_to=routed_to)
        )
    return result.rowcount > 0


async def archive_capture(user_id: int, capture_id: int) -> bool:
    """Set a user-owned capture's status to 'archived'. Returns True if updated."""
    async with get_session() as session:
        result = await session.execute(
            update(Capture)
            .where(Capture.id == capture_id, Capture.user_id == user_id)
            .values(status="archived")
        )
    return result.rowcount > 0
