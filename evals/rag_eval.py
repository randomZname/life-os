"""RAG hybrid measurement harness — vector-only vs hybrid, side by side.

NOT part of the pytest path. A human-in-the-loop aid: for a list of real
Bulgarian FMI queries it prints the top-k from VECTOR-ONLY next to the top-k
from HYBRID (vector ∪ trigram), so Богдан can eyeball whether the trigram half
actually helps. No pass/fail assertion.

Requires a live Postgres with ingested documents (the LEAD runs this at
integration). It guards every query: an empty/missing DB prints a clear note
and never crashes the whole script.

Run:
    .venv\\Scripts\\python.exe -m evals.rag_eval

Богдан: edit QUERIES below to taste.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from bogi.db import get_session
from bogi.models import Chunk, Document
from bogi.modules.documents import document_search, embed_texts

# Real FMI queries — edit freely.
QUERIES: list[str] = [
    "нормални форми бази данни",
    "централна гранична теорема",
    "виртуални функции C++",
    "наследяване и полиморфизъм Java",
    "B-дървета индекси",
    "доверителен интервал",
    "транзакции ACID свойства",
    "хеш таблици колизии",
]

TOP_K = 5
_SNIPPET_LEN = 90


def _snippet(text: str) -> str:
    s = " ".join((text or "").split())
    return s[:_SNIPPET_LEN] + ("…" if len(s) > _SNIPPET_LEN else "")


async def _vector_only(query: str, k: int = TOP_K) -> list[dict[str, Any]]:
    """Pure vector (cosine) retrieval — the baseline to compare hybrid against."""
    [query_emb] = embed_texts([query])
    async with get_session() as session:
        stmt = (
            select(
                Chunk.id,
                Chunk.text,
                Chunk.chunk_idx,
                Document.id.label("document_id"),
                Document.title,
                Document.file_path,
                Chunk.embedding.cosine_distance(query_emb).label("distance"),
            )
            .join(Document, Chunk.document_id == Document.id)
            .order_by(Chunk.embedding.cosine_distance(query_emb))
            .limit(k)
        )
        rows = (await session.execute(stmt)).all()
    return [
        {
            "chunk_id": r.id,
            "document_id": r.document_id,
            "title": r.title,
            "file_path": r.file_path,
            "chunk_idx": r.chunk_idx,
            "text": r.text,
            "score": 1.0 - float(r.distance),
        }
        for r in rows
    ]


async def _doc_count() -> int | None:
    """How many chunks exist. None signals the DB is unreachable."""
    try:
        async with get_session() as session:
            rows = (await session.execute(select(Chunk.id).limit(1))).all()
        return len(rows)
    except Exception as exc:  # DB down / not migrated
        print(f"[!] Cannot reach the database / chunks table: {exc}")
        return None


def _format_side(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["    (no results)"]
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(
            f"    {i}. [{r['score']:.3f}] {r.get('title') or '?'} "
            f"#{r.get('chunk_idx')} — {_snippet(r.get('text', ''))}"
        )
    return lines


async def main() -> None:
    print("RAG hybrid eval — vector-only vs hybrid\n" + "=" * 60)

    count = await _doc_count()
    if count is None:
        print("DB unreachable — start Postgres (Docker) and apply migrations first.")
        return
    if count == 0:
        print("DB reachable but `chunks` is EMPTY — ingest some documents first.")
        return

    for query in QUERIES:
        print(f"\n### Query: {query!r}")
        try:
            vec = await _vector_only(query, TOP_K)
        except Exception as exc:
            print(f"  vector-only failed: {exc}")
            vec = []
        try:
            hyb = await document_search(query, TOP_K)
        except Exception as exc:
            print(f"  hybrid failed: {exc}")
            hyb = []

        print("  VECTOR-ONLY:")
        for line in _format_side(vec):
            print(line)
        print("  HYBRID (vector ∪ trigram):")
        for line in _format_side(hyb):
            print(line)


if __name__ == "__main__":
    asyncio.run(main())
