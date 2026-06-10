"""Approval executor registry — runs an approved action for real.

Framework-agnostic: no pydantic_ai / litellm imports. Maps an approval's
`tool_name` to a concrete async executor. The Telegram callback dispatches
through `run()` exactly once when an approval transitions to approved.

Back-compat: `request_external_action` rows have no executor, so
`has_executor` is False and they are skipped (unchanged behaviour).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from bogi.modules import gcal, gmail


async def _exec_calendar_update(payload: dict[str, Any]) -> str:
    ev = await gcal.update_event(**payload)
    return f"Събитие обновено: {ev.get('summary', '')} → {ev.get('start', '')}"


async def _exec_calendar_delete(payload: dict[str, Any]) -> str:
    await gcal.delete_event(**payload)
    return f"Събитие изтрито: {payload.get('event_id', '')}"


async def _exec_gmail_send(payload: dict[str, Any]) -> str:
    sent = await gmail.send_message(**payload)
    return f"📧 Изпратен имейл до {sent.get('to', '')}: {sent.get('subject', '')}"


EXECUTORS: dict[str, Callable[[dict[str, Any]], Awaitable[str]]] = {
    "calendar.update_event": _exec_calendar_update,
    "calendar.delete_event": _exec_calendar_delete,
    "gmail.send": _exec_gmail_send,
}


def has_executor(tool_name: str) -> bool:
    return tool_name in EXECUTORS


async def run(tool_name: str, payload: dict[str, Any]) -> str:
    fn = EXECUTORS.get(tool_name)
    if fn is None:
        raise KeyError(f"no executor for {tool_name!r}")
    return await fn(payload)
