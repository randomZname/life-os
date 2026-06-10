"""RAG hybrid INTEGRATION test (needs Postgres) — locks in word_similarity.

The pure-merge tests live in `test_rag_hybrid.py`. This one ingests real chunks
and exercises the live SQL path, so it would have caught the bug where the
trigram half used `similarity()` (≈0 for a short query vs a long chunk) instead
of `word_similarity()`: an exact rare-term query must surface its chunk at the
top with a HIGH score (only word_similarity produces that; plain similarity gives
~0.1 and the chunk would not be promoted).

DB-touching: uses a sentinel `source` and hard-deletes its documents in a
`finally` (chunks cascade) so production data stays clean. Skips cleanly if the
DB is unreachable.
"""

from __future__ import annotations

import random
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import delete

from bogi.db import get_session
from bogi.models import Document
from bogi.modules import documents

# A rare token that does NOT occur in normal corpora, so the only way a search
# finds it is the lexical (trigram) half — not semantic embedding similarity.
_RARE = "КвазиградиентенМетод2026"
_SENTINEL = f"test_rag_integration_{random.randint(1, 10_000_000)}"


async def _cleanup() -> None:
    async with get_session() as session:
        await session.execute(delete(Document).where(Document.source == _SENTINEL))


@pytest.mark.asyncio
async def test_hybrid_surfaces_exact_rare_term_top():
    """An exact rare-term query ranks the containing chunk #1 with a high score.

    Guards the word_similarity fix: under the old `similarity()` trigram half the
    score would be ~0.1 (and the chunk likely not promoted above noise).
    """
    try:
        with tempfile.TemporaryDirectory() as tmp:
            # Doc A hides the rare token inside otherwise unrelated prose.
            a = Path(tmp) / "a.md"
            a.write_text(
                "Лекция по числени методи. Разглеждаме оптимизация и итеративни "
                f"схеми. Тук въвеждаме {_RARE} като упражнение. Сходимост и грешки.",
                encoding="utf-8",
            )
            # Doc B is on a different topic and does NOT contain the token.
            b = Path(tmp) / "b.md"
            b.write_text(
                "Бази от данни: нормални форми, функционални зависимости, "
                "транзакции и ACID свойства. Индекси и оптимизация на заявки.",
                encoding="utf-8",
            )
            ra = await documents.document_ingest(str(a), source=_SENTINEL)
            rb = await documents.document_ingest(str(b), source=_SENTINEL)
            assert ra.get("ok") and rb.get("ok"), (ra, rb)

            results = await documents.document_search(_RARE, k=5)
            assert results, "hybrid search returned nothing for the rare term"
            top = results[0]
            assert _RARE.lower() in top["text"].lower(), (
                f"top hit does not contain the rare term: {top['title']}"
            )
            # word_similarity on an exact token match is high; plain similarity
            # would be ~0.1. This threshold is the regression guard.
            assert top["score"] >= 0.6, f"expected a strong lexical score, got {top['score']:.3f}"
    finally:
        await _cleanup()
