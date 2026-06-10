"""Unit tests for the RAG hybrid merge — pure, NO database required.

Exercises `_merge_hybrid`: dedup by chunk_id (max score wins), descending
order, top-k truncation, and inclusion of trigram-only hits.
"""

from __future__ import annotations

from bogi.modules.documents import _merge_hybrid


def _row(chunk_id: int, score: float, *, title: str = "doc") -> dict:
    """Build a row in the exact shape document_search emits."""
    return {
        "chunk_id": chunk_id,
        "document_id": chunk_id * 10,
        "title": title,
        "file_path": f"/docs/{title}.pdf",
        "chunk_idx": 0,
        "text": f"text-{chunk_id}",
        "score": score,
    }


def test_merge_dedup_keeps_max_score():
    """A chunk found by BOTH paths appears once, with the higher score."""
    vector = [_row(1, 0.40)]
    trigram = [_row(1, 0.90)]
    out = _merge_hybrid(vector, trigram, k=5)
    assert len(out) == 1
    assert out[0]["chunk_id"] == 1
    assert out[0]["score"] == 0.90


def test_merge_dedup_max_when_vector_higher():
    """Max wins regardless of which side carried the bigger score."""
    vector = [_row(7, 0.95)]
    trigram = [_row(7, 0.10)]
    out = _merge_hybrid(vector, trigram, k=5)
    assert len(out) == 1
    assert out[0]["score"] == 0.95


def test_merge_descending_order():
    vector = [_row(1, 0.30), _row(2, 0.80)]
    trigram = [_row(3, 0.55)]
    out = _merge_hybrid(vector, trigram, k=5)
    scores = [r["score"] for r in out]
    assert scores == sorted(scores, reverse=True)
    assert [r["chunk_id"] for r in out] == [2, 3, 1]


def test_merge_top_k_truncation():
    vector = [_row(i, score=i / 10.0) for i in range(1, 8)]  # 7 distinct rows
    out = _merge_hybrid(vector, [], k=3)
    assert len(out) == 3
    # Highest scores survive: chunk_ids 7, 6, 5 (scores 0.7, 0.6, 0.5).
    assert [r["chunk_id"] for r in out] == [7, 6, 5]


def test_merge_includes_trigram_only_hit():
    """A trigram hit that vector never returned must still surface."""
    vector = [_row(1, 0.60)]
    trigram = [_row(2, 0.40)]  # only present in trigram path
    out = _merge_hybrid(vector, trigram, k=5)
    ids = {r["chunk_id"] for r in out}
    assert ids == {1, 2}


def test_merge_empty_inputs():
    assert _merge_hybrid([], [], k=5) == []


def test_merge_vector_only_passthrough():
    vector = [_row(1, 0.9), _row(2, 0.5)]
    out = _merge_hybrid(vector, [], k=5)
    assert [r["chunk_id"] for r in out] == [1, 2]


def test_merge_preserves_row_shape():
    """Merged rows keep the exact keys agent.py depends on."""
    out = _merge_hybrid([_row(1, 0.5)], [], k=5)
    assert set(out[0].keys()) == {
        "chunk_id",
        "document_id",
        "title",
        "file_path",
        "chunk_idx",
        "text",
        "score",
    }
