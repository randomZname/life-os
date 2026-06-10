"""Observability — the ONLY module that touches Logfire.

Off by default. With no ``LOGFIRE_TOKEN`` (or the ``logfire`` SDK not installed)
every helper here is a no-op, so the bot runs exactly as before and **no data
leaves the machine**. Turn it on later with::

    pip install -e ".[observability]"
    # then set LOGFIRE_TOKEN in .env

Keeps Logfire isolated from ``bogi/modules/*`` (same spirit as the framework-
import rule in CLAUDE.md): only this file imports ``logfire``; everything else
calls these thin helpers. See ``docs/OBSERVABILITY_PLAN.md``.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_enabled = False
_configured = False

# Secret-ish value patterns scrubbed from spans on top of Logfire's defaults.
_SCRUB_PATTERNS = [
    r"sk-ant-[\w-]+",  # Anthropic
    r"sk-[A-Za-z0-9-]{16,}",  # OpenAI / generic
    r"ya29\.[\w.-]+",  # Google OAuth access token
    r"ghp_[A-Za-z0-9]+",  # GitHub PAT
    r"xox[baprs]-[\w-]+",  # Slack
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",  # PEM
]


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    """True only after a successful configure with a token present."""
    return _enabled


def configure_logfire() -> bool:
    """Configure Logfire once, iff a token is present and the SDK is installed.

    Returns True when tracing is active, False when this is a no-op. Idempotent —
    safe to call from every entrypoint.
    """
    global _enabled, _configured
    if _configured:
        return _enabled
    _configured = True

    token = os.environ.get("LOGFIRE_TOKEN", "").strip()
    if not token or _truthy(os.environ.get("LOGFIRE_DISABLED")):
        logger.info("Logfire off (no LOGFIRE_TOKEN) — observability is a no-op")
        return False

    try:
        import logfire
    except ImportError:
        logger.warning(
            "LOGFIRE_TOKEN set but `logfire` not installed — "
            'run: pip install -e ".[observability]". Tracing stays off.'
        )
        return False

    try:
        logfire.configure(
            token=token,
            service_name="bogi-agent",
            scrubbing=logfire.ScrubbingOptions(extra_patterns=_SCRUB_PATTERNS),
        )
        # Pydantic AI is by the same authors — this auto-traces every agent run,
        # model call and tool call as OpenTelemetry spans.
        logfire.instrument_pydantic_ai()
        logfire.instrument_httpx()
    except Exception:
        logger.exception("Logfire configuration failed — continuing without tracing")
        return False

    _enabled = True
    logger.info("Logfire observability active (service=bogi-agent)")
    return True


def _clean(attributes: dict[str, Any]) -> dict[str, Any]:
    """Drop None values. Callers pass shapes/ids only — never bodies/secrets."""
    return {k: v for k, v in attributes.items() if v is not None}


def span(name: str, **attributes: Any):
    """Open a tracing span when Logfire is active; otherwise a no-op context.

    Pass ONLY safe metadata (ids, counts, source, flags) — never message text,
    email/document bodies, tokens, or any secret.
    """
    if not _enabled:
        return contextlib.nullcontext()
    try:
        import logfire

        return logfire.span(name, **_clean(attributes))
    except Exception:
        return contextlib.nullcontext()
