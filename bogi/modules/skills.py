"""Skills runtime — progressive disclosure of reusable know-how (V2 §2.F).

Framework-agnostic: no pydantic_ai / litellm imports. The agent layer exposes
`skill_list` / `skill_read` tools that call into here.

A *skill* is a folder `skills/<name>/SKILL.md` with YAML-ish frontmatter:

    ---
    name: university-email-bg
    description: Draft a formal Bulgarian email to an FMI professor.
    ---
    <markdown body: the actual instructions, templates, examples>

Progressive disclosure: only `name + description` of every skill go into the
system prompt (cheap). The full body is loaded on demand via `read_skill(name)`
when the agent decides a skill is relevant. This keeps the prompt small while
giving the agent a large library it can pull from.

Frontmatter is parsed by hand (no PyYAML dependency).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Repo-root/skills  (this file is bogi/modules/skills.py -> parents[2] == repo root)
SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

_NAME_RE_KEYS = ("name", "description")


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a SKILL.md into (frontmatter dict, body).

    Accepts a leading `---` ... `---` block of simple `key: value` lines.
    Unknown/extra keys are kept. If there's no frontmatter, returns ({}, text).
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, str] = {}
    body_start = len(lines)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body_start = i + 1
            break
        if ":" in lines[i]:
            key, _, val = lines[i].partition(":")
            meta[key.strip()] = val.strip()
    body = "\n".join(lines[body_start:]).strip()
    return meta, body


def _skill_files() -> list[Path]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def list_skills() -> list[dict[str, Any]]:
    """All skills as {name, description, slug}. Cheap — reads only frontmatter."""
    out: list[dict[str, Any]] = []
    for f in _skill_files():
        slug = f.parent.name
        try:
            meta, _ = _parse_frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        out.append(
            {
                "slug": slug,
                "name": meta.get("name", slug),
                "description": meta.get("description", "").strip(),
            }
        )
    return out


def skills_catalog() -> str:
    """Compact `- name: description` catalog for the system prompt (or '')."""
    skills = list_skills()
    if not skills:
        return ""
    lines = [f"- {s['name']}: {s['description']}" for s in skills]
    return "\n".join(lines)


def read_skill(name: str) -> str:
    """Full SKILL.md body for a skill, matched by slug or frontmatter name.

    Returns the markdown body (instructions/templates), or a clear not-found
    message listing available skills. Never raises on a missing skill.
    """
    target = name.strip().lower()
    for f in _skill_files():
        slug = f.parent.name
        try:
            meta, body = _parse_frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        if target in (slug.lower(), meta.get("name", "").strip().lower()):
            title = meta.get("name", slug)
            return f"# {title}\n\n{body}" if body else f"# {title}\n\n(празен skill)"
    available = ", ".join(s["slug"] for s in list_skills()) or "(няма)"
    return f"Няма skill '{name}'. Налични: {available}"
