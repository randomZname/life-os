"""File operations: save text content, download URLs, list saved files.

All operations are sandboxed to `settings.files_path` (default: ./data/files/).
Path traversal (../) is rejected so the agent can't escape the sandbox.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

from bogi.config import settings

logger = logging.getLogger(__name__)


def _resolve_safe(relative_path: str) -> Path:
    """Resolve relative_path against files_path. Reject anything that escapes."""
    if not relative_path or relative_path.strip() in (".", "/"):
        raise ValueError("relative_path is empty")
    # Strip leading slashes and normalize
    rel = relative_path.lstrip("/\\").strip()
    root = settings.files_path.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f"path escapes sandbox: {relative_path}")
    return target


def _safe_filename_from_url(url: str) -> str:
    """Extract a reasonable filename from a URL, falling back to 'downloaded.bin'."""
    name = url.rstrip("/").split("/")[-1].split("?")[0]
    name = re.sub(r"[^\w.\-]+", "_", name)
    return name or "downloaded.bin"


async def file_save(content: str, relative_path: str) -> dict:
    """Write text content to <files_path>/<relative_path>. Parent dirs auto-created."""
    target = _resolve_safe(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(target), "bytes": len(content.encode("utf-8"))}


async def file_save_bytes(data: bytes, relative_path: str) -> dict:
    """Write binary bytes to <files_path>/<relative_path>."""
    target = _resolve_safe(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"ok": True, "path": str(target), "bytes": len(data)}


async def file_download(url: str, relative_path: str | None = None) -> dict:
    """Download URL into the sandbox. If relative_path omitted, derive from URL."""
    if not relative_path:
        relative_path = _safe_filename_from_url(url)
    target = _resolve_safe(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            target.write_bytes(r.content)
            return {
                "ok": True,
                "path": str(target),
                "bytes": len(r.content),
                "content_type": r.headers.get("content-type", ""),
            }
    except Exception as exc:
        logger.exception("file_download failed: %s", url)
        return {"ok": False, "url": url, "error": str(exc)}


def file_list(subdir: str = "") -> dict:
    """List files in <files_path>/<subdir>. Returns relative paths."""
    root = settings.files_path
    base = _resolve_safe(subdir) if subdir else root
    if not base.exists():
        return {"ok": False, "error": f"path does not exist: {base}"}
    entries: list[dict] = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            entries.append({
                "path": str(p.relative_to(root)),
                "bytes": p.stat().st_size,
            })
    return {"ok": True, "count": len(entries), "files": entries}


def file_read(relative_path: str, max_chars: int = 50_000) -> dict:
    """Read text content of a file in the sandbox."""
    target = _resolve_safe(relative_path)
    if not target.is_file():
        return {"ok": False, "error": f"not a file: {relative_path}"}
    try:
        text = target.read_text(encoding="utf-8")
        truncated = len(text) > max_chars
        return {
            "ok": True,
            "path": str(target),
            "text": text[:max_chars],
            "truncated": truncated,
            "total_chars": len(text),
        }
    except UnicodeDecodeError:
        return {"ok": False, "error": "binary file, cannot decode as text"}
