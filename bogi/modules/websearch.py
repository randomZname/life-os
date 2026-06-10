"""Web search via DuckDuckGo (keyless).

Framework-agnostic: no pydantic_ai / litellm imports. The lead wires
``web_search`` as an agent tool. All external (web) text is passed through
``sanitize`` because it is untrusted content.
"""

from __future__ import annotations

import asyncio
import logging

from bogi.modules.sanitize import sanitize

log = logging.getLogger(__name__)


def _search_sync(query: str, max_results: int) -> list[dict]:
    """Blocking DuckDuckGo search. Runs in a thread via ``web_search``."""
    from ddgs import DDGS

    with DDGS() as d:
        return list(d.text(query, max_results=max_results))


async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web and return sanitized results.

    Each result is ``{"title": str, "url": str, "snippet": str}``. Web text is
    untrusted, so titles and snippets are run through ``sanitize``. On any
    error the function logs and returns ``[]`` — it never raises to the agent.
    """
    try:
        raw = await asyncio.to_thread(_search_sync, query, max_results)
    except Exception:
        log.exception("web_search failed for query=%r", query)
        return []

    results: list[dict] = []
    for item in raw[:max_results]:
        results.append(
            {
                "title": sanitize(str(item.get("title", ""))),
                "url": str(item.get("href", "")),
                "snippet": sanitize(str(item.get("body", ""))),
            }
        )
    return results
