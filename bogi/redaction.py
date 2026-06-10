"""Secret redaction for logs and traces (stdlib-only, framework-agnostic).

Provides a central ``redact()`` helper and a ``RedactingFilter`` logging filter
so secret-looking substrings never land in ``bot.log`` or the console.

The pattern list is the team-shared SECRET PATTERNS contract — keep it in sync
with the pre-commit content scanner.
"""

from __future__ import annotations

import logging
import re

# Team contract — EXACTLY these patterns (shared with the pre-commit scanner).
SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{24,}"),          # Anthropic
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                # OpenAI / generic
    re.compile(r"ya29\.[A-Za-z0-9_-]{20,}"),           # Google OAuth
    re.compile(r"ghp_[A-Za-z0-9]{36}"),                # GitHub PAT
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),       # Slack
    re.compile(r"AKIA[0-9A-Z]{16}"),                   # AWS access key id
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # PEM private key header
]

_REPLACEMENT = "[REDACTED]"


def redact(text: str) -> str:
    """Replace every secret-looking substring in ``text`` with ``[REDACTED]``.

    Safe on non-str input (coerced via ``str()``).
    """
    if not isinstance(text, str):
        text = str(text)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(_REPLACEMENT, text)
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs secrets from each record's msg and args.

    Attached to handlers so it applies to everything they emit. Must never
    raise: on any error the record is let through unchanged (logging must not
    break the app).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)

            args = record.args
            if isinstance(args, tuple):
                record.args = tuple(
                    redact(a) if isinstance(a, str) else a for a in args
                )
            elif isinstance(args, dict):
                record.args = {
                    k: (redact(v) if isinstance(v, str) else v)
                    for k, v in args.items()
                }
        except Exception:
            # Logging must never break the app — let the record through as-is.
            pass
        return True
