"""Post-turn auto-save pipeline.

Runs fire-and-forget after each agent turn (parallel to summarization).
Responsibilities:
1. Fast pre-filter (length, smalltalk, secret patterns) — no LLM cost.
2. Cheap LLM triage — decides save/skip + namespace + importance.
3. Save-or-update with embedding dedup.

Never blocks the user response. Any failure is logged and swallowed.
"""

from __future__ import annotations

import logging
from typing import Any

from bogi.modules import long_term_memory as ltm

logger = logging.getLogger(__name__)


async def process_turn(
    user_id: int,
    user_text: str,
    assistant_text: str,
    *,
    source_turn_id: int | None = None,
) -> dict[str, Any]:
    """Run the auto-memory pipeline on one finished turn.

    Returns a small dict with what happened — handy for tests/logging.
    Never raises.
    """
    result: dict[str, Any] = {
        "saved": False,
        "action": None,
        "memory_id": None,
        "namespace": None,
        "reason": None,
    }
    try:
        # Phase 1: cheap pre-filter on the USER message only. Assistant output
        # tends to repeat user content + add noise; pre-filtering on user side
        # avoids wasting LLM calls on assistant smalltalk.
        allow, why = ltm.should_save_memory(user_text)
        if not allow:
            result["reason"] = f"prefilter: {why}"
            return result

        # Phase 2: LLM triage. Considers both sides — assistant context can
        # disambiguate ("обядвахме в Х" → location vs venue suggestion).
        classified = await ltm.classify_memory(user_text, assistant_text)
        if classified is None:
            result["reason"] = "classifier said skip or failed"
            return result

        # Phase 3: persist.
        mem_id, action = await ltm.save_or_update(
            user_id=user_id,
            content=classified["content"],
            namespace=classified["namespace"],
            kind=classified["kind"],
            importance_score=classified["importance"],
            summary=classified["summary"],
            source="auto",
            source_turn_id=source_turn_id,
        )
        result.update(
            saved=True,
            action=action,
            memory_id=mem_id,
            namespace=classified["namespace"],
            reason=classified.get("reason") or "",
        )
        logger.info(
            "auto-memory: %s id=%s user=%s ns=%s",
            action, mem_id, user_id, classified["namespace"],
        )
    except Exception:
        logger.exception("memory_pipeline.process_turn failed (user_id=%s)", user_id)
        result["reason"] = "exception"
    return result
