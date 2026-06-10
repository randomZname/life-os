"""Persistent web/price monitors.

A Monitor watches a webpage (or a price on a page) and surfaces a change when
the page's tracked value differs from the last seen value. Framework-agnostic:
no pydantic_ai / litellm imports. Page fetching is delegated to
``bogi.modules.browser.browser_fetch`` (module→module import is allowed).

Public API:
    add_monitor(user_id, name, target_url, *, kind="webpage", rule=None) -> int
    list_monitors(user_id, *, active_only=True) -> list[dict]
    remove_monitor(user_id, monitor_id) -> bool          # soft-delete
    current_value(text, rule) -> str                     # PURE helper, no IO
    check_monitor(monitor, fetch) -> dict                # fetch+compare+persist
    check_all(user_id) -> list[dict]                     # only CHANGED monitors

First-run semantics (documented choice):
    When a monitor has never been checked (``last_value`` is None), the first
    ``check_monitor`` call establishes a baseline and reports ``changed=False``.
    Rationale: there is no prior value to have "changed" from, so a brand-new
    monitor should not spam the user on its very first poll. The baseline is
    still persisted so the *next* differing value is detected as a change.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime

from sqlalchemy import select, update

from bogi.db import get_session
from bogi.models import Monitor
from bogi.modules import browser

logger = logging.getLogger(__name__)

# Collapses any run of whitespace (incl. newlines) — used inside a single line.
_WS_RE = re.compile(r"[ \t\f\v]+")


# --- DB-backed CRUD ----------------------------------------------------------


async def add_monitor(
    user_id: int,
    name: str,
    target_url: str,
    *,
    kind: str = "webpage",
    rule: str | None = None,
) -> int:
    """Create a monitor and return its new id."""
    async with get_session() as session:
        monitor = Monitor(
            user_id=user_id,
            name=name,
            kind=kind,
            target_url=target_url,
            rule=rule,
            active=True,
        )
        session.add(monitor)
        await session.flush()
        return monitor.id


async def list_monitors(user_id: int, *, active_only: bool = True) -> list[dict]:
    """List a user's monitors as plain dicts (newest first)."""
    async with get_session() as session:
        stmt = select(Monitor).where(Monitor.user_id == user_id)
        if active_only:
            stmt = stmt.where(Monitor.active.is_(True))
        stmt = stmt.order_by(Monitor.id.desc())
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "id": m.id,
                "name": m.name,
                "kind": m.kind,
                "target_url": m.target_url,
                "rule": m.rule,
                "last_value": m.last_value,
                "last_checked_at": m.last_checked_at,
                "active": m.active,
            }
            for m in rows
        ]


async def remove_monitor(user_id: int, monitor_id: int) -> bool:
    """Soft-delete (active=False). Returns True if a row was updated."""
    async with get_session() as session:
        result = await session.execute(
            update(Monitor)
            .where(Monitor.user_id == user_id, Monitor.id == monitor_id)
            .values(active=False)
        )
        return result.rowcount > 0


# --- Pure value extraction (no DB / no IO) -----------------------------------


def _normalize(text: str) -> str:
    """Normalize whitespace: trim each line, drop blank lines, collapse runs."""
    lines = [_WS_RE.sub(" ", line).strip() for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line)


def current_value(text: str, rule: str | None) -> str:
    """Compute the tracked value of a page. PURE — unit-testable, no IO.

    - If ``rule`` is a non-empty keyword: return the first normalized line that
      contains the keyword (case-insensitive), or "" if none does.
    - Otherwise: return a stable short signature of the whole normalized page:
      ``"<len>:<sha1[:16]>"``.
    """
    normalized = _normalize(text)
    keyword = (rule or "").strip()
    if keyword:
        needle = keyword.lower()
        for line in normalized.split("\n"):
            if needle in line.lower():
                return line
        return ""
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{len(normalized)}:{digest}"


# --- Checking ----------------------------------------------------------------


async def check_monitor(monitor: dict, fetch) -> dict:
    """Fetch a single monitor's page, compute its value, compare, and persist.

    ``monitor`` is a dict with keys (id, target_url, rule, last_value[, name]).
    ``fetch`` is an async callable ``url -> text``.

    Returns {monitor_id, name?, changed, old, new}. On fetch error returns
    {monitor_id, name?, changed: False, error: str(exc)} and does not raise.
    The new value + last_checked_at are persisted for the monitor's id.
    """
    monitor_id = monitor["id"]
    name = monitor.get("name")
    base: dict = {"monitor_id": monitor_id}
    if name is not None:
        base["name"] = name

    try:
        text = await fetch(monitor["target_url"])
    except Exception as exc:  # never let one fetch abort a batch
        logger.warning("monitor %s fetch failed: %s", monitor_id, exc)
        return {**base, "changed": False, "error": str(exc)}

    old = monitor.get("last_value")
    new = current_value(text, monitor.get("rule"))

    # First run (no baseline yet) establishes the baseline without flagging
    # a change — see module docstring.
    changed = old is not None and new != old

    async with get_session() as session:
        await session.execute(
            update(Monitor)
            .where(Monitor.id == monitor_id)
            .values(last_value=new, last_checked_at=datetime.utcnow())
        )

    return {**base, "changed": changed, "old": old, "new": new}


async def check_all(user_id: int) -> list[dict]:
    """Check all active monitors for a user; return only the CHANGED ones."""
    monitors = await list_monitors(user_id, active_only=True)

    async def _fetch(url: str) -> str:
        result = await browser.browser_fetch(url)
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "fetch failed"))
        return result.get("text", "")

    changed: list[dict] = []
    for monitor in monitors:
        try:
            outcome = await check_monitor(monitor, _fetch)
        except Exception as exc:  # isolate per-monitor failures
            logger.warning("monitor %s check errored: %s", monitor.get("id"), exc)
            continue
        if outcome.get("changed"):
            changed.append(outcome)
    return changed
