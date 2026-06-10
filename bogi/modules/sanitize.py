"""Tool-output sanitization v2 — neutralize prompt injection in untrusted text.

Tool outputs are UNTRUSTED data, not instructions. This module hardens the
Lethal Trifecta defense: before external text (Moodle, web, documents, calendar
descriptions, …) reaches the model, we truncate it and neutralize common
prompt-injection triggers so embedded instructions become inert while the
surrounding data is preserved.

stdlib-only, framework-agnostic (no pydantic_ai / litellm) — per the layering
rule for `bogi/modules/*`.

Public contract (the integrator builds to this):
    find_injections(text: str) -> list[str]
    sanitize(text: str, *, max_chars: int = 50_000) -> str
"""

from __future__ import annotations

import re

# Each entry: (label, compiled_regex). Patterns are case-insensitive and
# multiline. They are intentionally conservative — false positives on ordinary
# prose are worse than missing an exotic injection, because the agent already
# wraps this text in <untrusted_content> as a second layer.
INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # EN: "ignore/disregard/forget (all) (the) previous/above instructions".
    # Require the word "instructions" nearby so we don't mangle "ignore the
    # previous email" etc.
    (
        "ignore_previous_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget)\b"
            r"(?:\s+\w+){0,4}?\s+"
            r"\b(?:previous|above|prior|earlier|all\s+(?:previous|prior)?)\b"
            r"(?:\s+\w+){0,3}?\s+"
            r"\binstructions?\b",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    # Line-start role spoofing: "System:", "Assistant:", "Human:", "User:".
    # Anchored to line start to avoid matching "the operating system is fast".
    (
        "role_spoof",
        re.compile(
            r"^\s*(?:system|assistant|human|user)\s*:",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    # Fake control / framing tags.
    (
        "fake_control_tag",
        re.compile(
            r"</?(?:tool_call|function_call|tool_result|system|untrusted_content)\b[^>]*>",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    # EN: "new instructions:".
    (
        "new_instructions",
        re.compile(r"\bnew\s+instructions?\s*:", re.IGNORECASE | re.MULTILINE),
    ),
    # EN: "you are now".
    (
        "you_are_now",
        re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE | re.MULTILINE),
    ),
    # EN: "act as".
    (
        "act_as",
        re.compile(r"\bact\s+as\b", re.IGNORECASE | re.MULTILINE),
    ),
    # BG: "игнорирай ... инструкции" / "пренебрегни ... инструкции".
    (
        "bg_ignore_instructions",
        re.compile(
            r"\b(?:игнорирай|пренебрегни)\b(?:\s+\S+){0,4}?\s+\bинструкции\b",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    # BG: "забрави ... инструкции".
    (
        "bg_forget_instructions",
        re.compile(
            r"\bзабрави\b(?:\s+\S+){0,4}?\s+\bинструкции\b",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    # BG: "нови инструкции".
    (
        "bg_new_instructions",
        re.compile(r"\bнови\s+инструкции\b", re.IGNORECASE | re.MULTILINE),
    ),
    # BG: "ти си вече" (≈ "you are now").
    (
        "bg_you_are_now",
        re.compile(r"\bти\s+си\s+вече\b", re.IGNORECASE | re.MULTILINE),
    ),
]

_PLACEHOLDER = "[neutralized]"


def find_injections(text: str) -> list[str]:
    """Return labels of every injection pattern that matches `text`.

    Deduped, stable order (the order patterns appear in INJECTION_PATTERNS).
    Returns an empty list for clean text. Never raises — coerces non-str.
    """
    if not isinstance(text, str):
        text = str(text)
    found: list[str] = []
    for label, pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            found.append(label)
    return found


def _neutralize(text: str) -> str:
    """Replace each matched injection trigger substring with `[neutralized]`.

    Conservative: only the matched trigger is replaced, surrounding data is
    preserved verbatim.
    """
    for _label, pattern in INJECTION_PATTERNS:
        text = pattern.sub(_PLACEHOLDER, text)
    return text


def sanitize(text: str, *, max_chars: int = 50_000) -> str:
    """Truncate to `max_chars`, then neutralize injection triggers.

    - Non-str input is coerced via ``str()``.
    - If longer than `max_chars`, the text is cut and a truncation marker
      ``\\n…[truncated {removed} chars]`` is appended.
    - Never raises.
    """
    if not isinstance(text, str):
        text = str(text)

    if max_chars >= 0 and len(text) > max_chars:
        removed = len(text) - max_chars
        text = text[:max_chars] + f"\n…[truncated {removed} chars]"

    return _neutralize(text)
