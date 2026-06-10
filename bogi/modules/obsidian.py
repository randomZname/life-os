"""Obsidian vault read/write.

Safety: write_draft пише САМО в `vault/inbox/`. Никога не пише в произволен path.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from bogi.config import settings

logger = logging.getLogger(__name__)


def _safe_relative(path: str) -> Path:
    """Нормализира path спрямо vault root и блокира пътища извън vault-а."""
    vault_root = settings.vault_root.resolve()
    candidate = (vault_root / path).resolve()
    try:
        candidate.relative_to(vault_root)
    except ValueError as exc:
        raise ValueError(f"Path '{path}' е извън vault-а") from exc
    return candidate


def _slugify(text: str, max_len: int = 80) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:max_len] or "draft"


def vault_read(path: str, max_chars: int = 50_000) -> dict:
    """Чете файл от vault-а."""
    try:
        full_path = _safe_relative(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if not full_path.exists():
        return {"ok": False, "error": f"Не съществува: {path}"}
    if not full_path.is_file():
        return {"ok": False, "error": f"Не е файл: {path}"}

    text = full_path.read_text(encoding="utf-8", errors="ignore")
    return {
        "ok": True,
        "path": path,
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
    }


def vault_write_draft(filename: str, content: str, subdir: str | None = None) -> dict:
    """Пише чернова САМО в inbox-папката на vault-а (или нейна подпапка).

    Inbox = `settings.vault_inbox_subdir` (по подразбиране „00_Inbox" — реалния
    Obsidian vault). `filename` се slugify-ва; ако съществува → timestamp.
    """
    inbox = settings.vault_inbox_subdir
    if subdir is None:
        subdir = inbox
    if subdir != inbox and not subdir.startswith(f"{inbox}/"):
        return {"ok": False, "error": f"vault_write_draft работи само в `{inbox}/`"}

    target_dir = settings.vault_root / subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    # Slugify име, запази разширението ако има
    name_stem = Path(filename).stem
    name_suffix = Path(filename).suffix or ".md"
    safe_name = _slugify(name_stem)

    target = target_dir / f"{safe_name}{name_suffix}"
    if target.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = target_dir / f"{safe_name}-{ts}{name_suffix}"

    target.write_text(content, encoding="utf-8")
    relative = str(target.relative_to(settings.vault_root))
    logger.info("Draft written to %s", relative)
    return {"ok": True, "path": relative, "absolute": str(target)}


def vault_list(subdir: str = "", limit: int = 100) -> dict:
    """Списък на файлове в подпапка на vault-а."""
    try:
        full_path = _safe_relative(subdir) if subdir else settings.vault_root
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if not full_path.exists():
        return {"ok": False, "error": f"Не съществува: {subdir}"}

    files: list[str] = []
    for p in sorted(full_path.rglob("*.md"))[:limit]:
        files.append(str(p.relative_to(settings.vault_root)))
    return {"ok": True, "files": files, "count": len(files)}
