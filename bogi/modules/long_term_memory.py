"""Long-term memory v1: namespace + importance + recency-ranked retrieval.

Distinct from `bogi.modules.memory` which holds the per-thread conversation
transcript. Items here survive `/new`, span all channels, and are recalled by
a composite score (cosine similarity + importance + recency).

Public API:
    save_memory(user_id, content, kind="fact", source_turn_id=None, pinned=False)
        — backward-compat thin wrapper around save_or_update.
    save_or_update(user_id, content, *, namespace, kind, importance_score,
                   summary, source, source_turn_id, pinned, dedup_threshold)
        -> (memory_id, action)  where action in {'created','updated','skipped'}
    recall_memories(user_id, query, k=5)  — backward-compat wrapper.
    retrieve_relevant(user_id, query, *, namespace_hint, limit,
                      recency_half_life_days) -> list[dict]
    list_memories(user_id, pinned_only=False, limit=50, namespace=None)
    forget_memory(memory_id, user_id) / forget_by_query(user_id, query, threshold)

    should_save_memory(text) -> tuple[bool, str]    — fast regex/heuristic reject
    classify_memory(user_text, assistant_text=None) -> dict | None
        — single LLM triage call returning {save, namespace, kind, content,
          summary, importance, reason}. None on hard reject or LLM failure.

Namespaces (canonical):
    study/statistics, study/databases, study/java, study/cpp
    projects/jarvis
    tasks/homework, tasks/deadlines
    personal/preferences
    procedures
    general (fallback)
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone

from sqlalchemy import func, select, update

from bogi.db import get_session
from bogi.models import Memory
from bogi.modules.documents import embed_texts

logger = logging.getLogger(__name__)

# --- Retrieval tuning ---------------------------------------------------------
DEFAULT_RECALL_K = 5
DEFAULT_RECENCY_HALF_LIFE_DAYS = 90.0
# Composite score weights — INVARIANT: W_COSINE+W_IMPORTANCE+W_RECENCY+W_USAGE == 1.0
W_COSINE = 0.55
W_IMPORTANCE = 0.20
W_RECENCY = 0.10
W_USAGE = 0.15
# Saturation constant for usage_factor: how fast frequently-recalled memories
# approach 1.0 (higher = slower saturation).
USAGE_SATURATION = 5.0
# Soft boost added to score when memory.namespace == namespace_hint
NAMESPACE_HINT_BOOST = 0.2
# pg_trgm similarity floor for the hybrid keyword candidate query (0..1).
KEYWORD_SIM_THRESHOLD = 0.1

# --- Dedup tuning -------------------------------------------------------------
# Cosine distance threshold: below this AND same namespace → update existing.
DUPLICATE_DISTANCE = 0.18

# --- Canonical namespace set --------------------------------------------------
NAMESPACES: tuple[str, ...] = (
    "study/statistics",
    "study/databases",
    "study/java",
    "study/cpp",
    "projects/jarvis",
    "tasks/homework",
    "tasks/deadlines",
    "personal/preferences",
    "procedures",
    "general",
)

KINDS: tuple[str, ...] = (
    "fact",
    "preference",
    "project",
    "skill",
    "procedure",
    "other",
)

# --- Secret / noise filters ---------------------------------------------------
# Heuristic regexes for things we MUST NOT persist. Ordered roughly by
# specificity. The goal is "no false negatives on common secret shapes" —
# false positives are fine (we just don't save).
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"GOCSPX-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"(?i)password\s*[:=]\s*\S{6,}"),
    re.compile(r"(?i)passwd\s*[:=]\s*\S{6,}"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*\S{20,}"),
    re.compile(r"(?i)\bsecret\s*[:=]\s*\S{20,}"),
    re.compile(r"(?i)\btoken\s*[:=]\s*\S{20,}"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),  # JWT
]

# Cheap noise filters (chat smalltalk that's never worth saving).
_NOISE_RE = re.compile(
    r"^\s*(ok|okay|yes|no|да|не|благодаря|thanks|thx|hi|hello|здр|здравей|"
    r"добре|чао|bye|sure|правилно|разбрах|ок)[\s!.?]*$",
    re.IGNORECASE,
)

MIN_CONTENT_LEN = 10
MAX_CONTENT_LEN = 1500


def should_save_memory(text: str) -> tuple[bool, str]:
    """Fast pre-LLM filter. Returns (allow, reason_if_blocked).

    Always called before classify_memory — saves a Claude/GPT round-trip
    on obvious rejects.
    """
    if not text:
        return False, "empty"
    stripped = text.strip()
    if len(stripped) < MIN_CONTENT_LEN:
        return False, "too short"
    if len(stripped) > MAX_CONTENT_LEN:
        return False, "too long (>1500 chars)"
    if _NOISE_RE.match(stripped):
        return False, "smalltalk noise"
    for pat in _SECRET_PATTERNS:
        if pat.search(stripped):
            return False, f"secret pattern matched: {pat.pattern[:40]}"
    return True, ""


# --- LLM classifier -----------------------------------------------------------

_CLASSIFY_SYSTEM = """Ти си memory triage за личен AI асистент на Богдан (студент във ФМИ).

Решаваш дали даден факт от разговор си струва да се запомни дългосрочно.

ОТХВЪРЛЯЙ (save=false):
- Паролаь, API key, token, OAuth secret, JWT — никога не запомняй.
- Тривиален small-talk ("здравей", "благодаря", "ок").
- One-off въпрос без бъдеща стойност.
- Информация, която е очевидна от контекста.
- Конкретно решение на конкретна задача (остава в transcript, не в memory).

ЗАПОМНЯЙ (save=true):
- Стабилно предпочитание (език, стил, инструменти).
- Име на човек, контакт, идентичност.
- Проектен контекст (повтарящи се хора, места, технологии).
- Техническо решение/процедура, която ще се ползва пак.
- Учебна бележка с трайна стойност.
- Deadline или recurring задача.

Namespaces (избери точно един):
- study/statistics, study/databases, study/java, study/cpp
- projects/jarvis
- tasks/homework, tasks/deadlines
- personal/preferences
- procedures
- general (fallback)

Kinds: fact | preference | project | skill | procedure | other

Отговори САМО с JSON. Без markdown, без коментар.
content = факта в ≤200 знака на български (или EN ако оригиналът е EN).
summary = 3-7 думи label.
importance = 0.0–1.0 (0.5 default; пинирани неизменни предпочитания = 0.9).
"""

_CLASSIFY_SCHEMA_HINT = """Формат:
{"save": bool, "namespace": "...", "kind": "...", "content": "...",
 "summary": "...", "importance": float, "reason": "..."}"""


async def classify_memory(user_text: str, assistant_text: str | None = None) -> dict | None:
    """Run cheap LLM triage. Returns dict on save=True; None otherwise.

    Always fail-soft: any exception → None (we silently skip saving).
    """
    allowed, why = should_save_memory(user_text)
    if not allowed:
        logger.debug("classify_memory: pre-filter rejected (%s)", why)
        return None

    user_block = f"USER: {user_text.strip()}"
    asst_block = f"\nASSISTANT: {assistant_text.strip()[:1500]}" if assistant_text else ""
    user_prompt = f"{_CLASSIFY_SCHEMA_HINT}\n\nРазговор:\n{user_block}{asst_block}"

    raw = await _llm_classify(_CLASSIFY_SYSTEM, user_prompt)
    if not raw:
        return None

    data = _safe_json_parse(raw)
    if not data:
        logger.warning("classify_memory: could not parse LLM output: %s", raw[:200])
        return None
    if not data.get("save"):
        return None

    ns = data.get("namespace") or "general"
    if ns not in NAMESPACES:
        logger.debug("classify_memory: unknown ns %r — defaulting to general", ns)
        ns = "general"
    kind = data.get("kind") or "fact"
    if kind not in KINDS:
        kind = "other"

    content = (data.get("content") or "").strip()
    if not content:
        return None

    try:
        importance = float(data.get("importance", 0.5))
    except (TypeError, ValueError):
        importance = 0.5
    importance = max(0.0, min(1.0, importance))

    summary = (data.get("summary") or "").strip()[:256] or None

    return {
        "save": True,
        "namespace": ns,
        "kind": kind,
        "content": content[:1500],
        "summary": summary,
        "importance": importance,
        "reason": (data.get("reason") or "")[:300],
    }


async def _llm_classify(system_text: str, user_text: str) -> str:
    """Cheap-alias call via LiteLLM. Bypasses agent loop."""
    import httpx
    from bogi.config import settings

    url = f"{settings.litellm_base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": "cheap",
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": 300,
        "temperature": 0.0,
    }
    headers = {"Authorization": f"Bearer {settings.litellm_master_key}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        logger.exception("classify LLM call failed")
        return ""


def _safe_json_parse(text: str) -> dict | None:
    """Robust JSON extraction. Models sometimes wrap in ```json ... ```."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    try:
        return json.loads(text)
    except Exception:
        # Try to extract first {...} block
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None


# --- Save / update ------------------------------------------------------------


async def _find_similar(
    user_id: int,
    namespace: str,
    embedding: list[float],
    threshold: float,
) -> Memory | None:
    """Return the closest active memory in same namespace if within threshold."""
    async with get_session() as session:
        stmt = (
            select(Memory, Memory.embedding.cosine_distance(embedding).label("dist"))
            .where(Memory.user_id == user_id)
            .where(Memory.archived.is_(False))
            .where(Memory.namespace == namespace)
            .order_by("dist")
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
    if not row:
        return None
    mem, dist = row
    if float(dist) <= threshold:
        return mem
    return None


async def save_or_update(
    user_id: int,
    content: str,
    *,
    namespace: str = "general",
    kind: str = "fact",
    importance_score: float = 0.5,
    summary: str | None = None,
    source: str = "manual",
    source_turn_id: int | None = None,
    pinned: bool = False,
    dedup_threshold: float = DUPLICATE_DISTANCE,
) -> tuple[int, str]:
    """Insert or merge a memory.

    Returns (memory_id, action) where action ∈ {'created','updated','skipped'}.
    Dedup: same namespace + cosine distance ≤ dedup_threshold → update existing.
    """
    content = content.strip()
    if not content:
        raise ValueError("memory content empty")
    if namespace not in NAMESPACES:
        namespace = "general"
    if kind not in KINDS:
        kind = "other"
    importance_score = max(0.0, min(1.0, float(importance_score)))

    embedding = embed_texts([content])[0]

    existing = await _find_similar(user_id, namespace, embedding, dedup_threshold)
    now = datetime.utcnow()

    if existing is not None:
        async with get_session() as session:
            await session.execute(
                update(Memory)
                .where(Memory.id == existing.id)
                .values(
                    content=content,
                    summary=summary if summary else existing.summary,
                    # Importance: take max of old vs new — promotions stick.
                    importance_score=max(existing.importance_score, importance_score),
                    pinned=existing.pinned or pinned,
                    source=source,
                    source_turn_id=source_turn_id or existing.source_turn_id,
                    embedding=embedding,
                    updated_at=now,
                )
            )
        logger.info(
            "Memory UPDATED id=%s user=%s ns=%s kind=%s importance=%.2f",
            existing.id, user_id, namespace, kind, importance_score,
        )
        return existing.id, "updated"

    async with get_session() as session:
        mem = Memory(
            user_id=user_id,
            content=content,
            namespace=namespace,
            kind=kind,
            importance_score=importance_score,
            summary=summary,
            source=source,
            source_turn_id=source_turn_id,
            pinned=pinned,
            embedding=embedding,
            created_at=now,
            updated_at=now,
        )
        session.add(mem)
        await session.flush()
        logger.info(
            "Memory CREATED id=%s user=%s ns=%s kind=%s importance=%.2f source=%s",
            mem.id, user_id, namespace, kind, importance_score, source,
        )
        return mem.id, "created"


# Backward-compat: existing /remember handler + tests call save_memory().
async def save_memory(
    user_id: int,
    content: str,
    kind: str = "fact",
    source_turn_id: int | None = None,
    pinned: bool = False,
) -> int:
    """Compat shim → save_or_update with namespace='general'."""
    mem_id, _ = await save_or_update(
        user_id,
        content,
        namespace="general",
        kind=kind,
        importance_score=0.7 if pinned else 0.5,
        source="manual",
        source_turn_id=source_turn_id,
        pinned=pinned,
    )
    return mem_id


# --- Retrieval ----------------------------------------------------------------


def _recency_factor(created_at: datetime | None, half_life_days: float) -> float:
    if not created_at:
        return 1.0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
    return math.exp(-age_days / max(1.0, half_life_days))


def usage_factor(access_count: int | None) -> float:
    """Map recall frequency → 0..1 saturating boost (diminishing returns).

    usage_factor(0)=0, then rises and asymptotically approaches 1.0 so a
    frequently-recalled memory ranks higher than a never-recalled peer, but the
    marginal gain shrinks: 1 - exp(-n / USAGE_SATURATION).
    """
    n = max(0, int(access_count or 0))
    return 1.0 - math.exp(-n / max(1.0, USAGE_SATURATION))


async def retrieve_relevant(
    user_id: int,
    query: str,
    *,
    namespace_hint: str | None = None,
    limit: int = DEFAULT_RECALL_K,
    recency_half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS,
) -> list[dict]:
    """Top-k relevant memories ranked by a composite score over a HYBRID
    candidate set (vector similarity ∪ keyword/trigram match).

    Score (non-pinned) = W_COSINE·cos_sim + W_IMPORTANCE·importance
                         + W_RECENCY·recency + W_USAGE·usage
                         + NAMESPACE_HINT_BOOST (if namespace == namespace_hint)
    Pinned memories always pass through (score floor at 1.0).

    Side effect: bumps access_count / last_accessed_at on the returned rows.
    A bump failure never breaks retrieval — results are returned regardless.
    """
    query = query.strip()
    if not query:
        return []
    if namespace_hint and namespace_hint not in NAMESPACES:
        namespace_hint = None

    q_emb = embed_texts([query])[0]

    # Pull a wider candidate set than `limit` so reranking has options.
    candidate_pool = max(limit * 4, 20)

    async with get_session() as session:
        pinned_stmt = (
            select(Memory)
            .where(Memory.user_id == user_id)
            .where(Memory.archived.is_(False))
            .where(Memory.pinned.is_(True))
        )
        pinned_rows = list((await session.execute(pinned_stmt)).scalars().all())

        # Vector candidates: nearest neighbours by cosine distance.
        sim_stmt = (
            select(Memory, Memory.embedding.cosine_distance(q_emb).label("dist"))
            .where(Memory.user_id == user_id)
            .where(Memory.archived.is_(False))
            .where(Memory.pinned.is_(False))
            .order_by("dist")
            .limit(candidate_pool)
        )
        sim_rows = list((await session.execute(sim_stmt)).all())

        # Keyword candidates: pg_trgm-similar content. Carry the SAME cosine
        # distance label so trigram hits get a real cos_sim for scoring. This
        # lets an exact-term memory that vector ranked low still surface.
        kw_stmt = (
            select(Memory, Memory.embedding.cosine_distance(q_emb).label("dist"))
            .where(Memory.user_id == user_id)
            .where(Memory.archived.is_(False))
            .where(Memory.pinned.is_(False))
            # pg_trgm similarity (uses the GIN trigram index); avoids the bare
            # `%` operator which renders as an unknown `%%` op under asyncpg.
            .where(func.similarity(Memory.content, query) > KEYWORD_SIM_THRESHOLD)
            .order_by(func.similarity(Memory.content, query).desc())
            .limit(candidate_pool)
        )
        try:
            kw_rows = list((await session.execute(kw_stmt)).all())
        except Exception:
            # If trigram search ever errors, fall back to vector-only
            # (never break recall).
            logger.warning("hybrid keyword candidate query failed", exc_info=True)
            kw_rows = []

    # Merge vector + keyword candidates, keyed by id, keeping the distance.
    pinned_ids = {m.id for m in pinned_rows}
    merged: dict[int, tuple[Memory, float]] = {}
    for mem, dist in [*sim_rows, *kw_rows]:
        if mem.id in pinned_ids or mem.id in merged:
            continue
        merged[mem.id] = (mem, float(dist))

    scored: list[tuple[float, Memory, float]] = []  # (final_score, mem, cos_sim)

    for mem in pinned_rows:
        scored.append((1.0, mem, 1.0))

    for mem, dist in merged.values():
        cos_sim = max(0.0, 1.0 - dist)
        recency = _recency_factor(mem.created_at, recency_half_life_days)
        usage = usage_factor(mem.access_count)
        score = (
            W_COSINE * cos_sim
            + W_IMPORTANCE * mem.importance_score
            + W_RECENCY * recency
            + W_USAGE * usage
        )
        if namespace_hint and mem.namespace == namespace_hint:
            score += NAMESPACE_HINT_BOOST
        scored.append((score, mem, cos_sim))

    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:limit]

    await _bump_usage([mem.id for _, mem, _ in top])

    return [
        {
            "id": mem.id,
            "content": mem.content,
            "summary": mem.summary,
            "namespace": mem.namespace,
            "kind": mem.kind,
            "importance": mem.importance_score,
            "pinned": mem.pinned,
            "access_count": mem.access_count,
            "score": round(final_score, 4),
            "cosine_sim": round(cos_sim, 4),
            "created_at": mem.created_at.isoformat() if mem.created_at else None,
        }
        for final_score, mem, cos_sim in top
    ]


async def _bump_usage(ids: list[int]) -> None:
    """Increment access_count and stamp last_accessed_at for the returned ids.

    Best-effort: a failure here must NEVER break retrieval, so all errors are
    swallowed (logged) and callers get their results regardless.
    """
    if not ids:
        return
    try:
        now = datetime.utcnow()
        async with get_session() as session:
            await session.execute(
                update(Memory)
                .where(Memory.id.in_(ids))
                .values(
                    access_count=Memory.access_count + 1,
                    last_accessed_at=now,
                )
            )
    except Exception:
        logger.warning("usage bump failed for ids=%s", ids, exc_info=True)


# Backward-compat: existing system_prompt hook calls recall_memories(user_id, query, k=3).
async def recall_memories(
    user_id: int,
    query: str,
    k: int = 3,
) -> list[dict]:
    return await retrieve_relevant(user_id, query, limit=k)


# --- Listing / forgetting (mostly unchanged) ----------------------------------


async def list_memories(
    user_id: int,
    pinned_only: bool = False,
    limit: int = 50,
    namespace: str | None = None,
) -> list[dict]:
    async with get_session() as session:
        stmt = (
            select(Memory)
            .where(Memory.user_id == user_id)
            .where(Memory.archived.is_(False))
        )
        if pinned_only:
            stmt = stmt.where(Memory.pinned.is_(True))
        if namespace and namespace in NAMESPACES:
            stmt = stmt.where(Memory.namespace == namespace)
        stmt = stmt.order_by(Memory.pinned.desc(), Memory.created_at.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": m.id,
            "content": m.content,
            "summary": m.summary,
            "namespace": m.namespace,
            "kind": m.kind,
            "importance": m.importance_score,
            "pinned": m.pinned,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in rows
    ]


async def forget_memory(memory_id: int, user_id: int) -> bool:
    async with get_session() as session:
        result = await session.execute(
            update(Memory)
            .where(Memory.id == memory_id)
            .where(Memory.user_id == user_id)
            .where(Memory.archived.is_(False))
            .values(archived=True, updated_at=datetime.utcnow())
        )
        return (result.rowcount or 0) > 0


async def forget_by_query(user_id: int, query: str, threshold: float = 0.85) -> int:
    matches = await retrieve_relevant(user_id, query, limit=1)
    if not matches:
        return 0
    top = matches[0]
    if top["score"] < threshold and not top["pinned"]:
        logger.info("forget_by_query: top score %.2f < %.2f", top["score"], threshold)
        return 0
    ok = await forget_memory(top["id"], user_id)
    return 1 if ok else 0
