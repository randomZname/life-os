"""Self-heal helpers: refresh Anthropic token + reload LiteLLM container.

Used by:
- `watchdog.py` — proactive refresh every 30 min
- `BogiAgent.run` — reactive recovery on 401 mid-session

Keep this file dependency-light. No pydantic_ai imports here.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
DOCKER_BIN = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"


def _read_anthropic_key() -> str:
    if not ENV_PATH.exists():
        return ""
    text = ENV_PATH.read_text(encoding="utf-8")
    m = re.search(r"^ANTHROPIC_API_KEY=(.*)$", text, flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


def _sync_token_blocking() -> str:
    """Run sync_oauth_token.sync() defensively. Returns the new token (or '' on failure)."""
    try:
        import sync_oauth_token
        sync_oauth_token.sync()
    except SystemExit:
        logger.warning("Token sync exited — keeping current .env value")
    except Exception as exc:
        logger.warning("Token sync failed: %s — keeping current .env value", exc)
    return _read_anthropic_key()


def _recreate_litellm_blocking() -> bool:
    """Force-recreate LiteLLM container. Returns True on success."""
    try:
        result = subprocess.run(
            [DOCKER_BIN, "compose", "up", "-d", "--force-recreate", "litellm"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("LiteLLM container recreated")
            return True
        logger.warning(
            "LiteLLM recreate non-zero exit: %s | %s",
            result.returncode,
            result.stderr[:500],
        )
        return False
    except Exception as exc:
        logger.warning("LiteLLM recreate failed: %s", exc)
        return False


def _wait_for_litellm_blocking(timeout_s: float = 60.0) -> bool:
    """Poll LiteLLM /v1/models until 200 or timeout."""
    import httpx

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(
                "http://localhost:4000/v1/models",
                headers={"Authorization": "Bearer sk-bogi-local-change-me"},
                timeout=5.0,
            )
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(2.0)
    return False


async def heal_anthropic_auth() -> bool:
    """Full self-heal: refresh OAuth token + recreate LiteLLM + wait healthy.

    Idempotent. Safe to call multiple times. Async wrapper around blocking ops.
    Returns True if LiteLLM is healthy at the end.
    """
    logger.info("heal_anthropic_auth: starting recovery")
    new_token = await asyncio.to_thread(_sync_token_blocking)
    if not new_token:
        logger.error("heal_anthropic_auth: no new token after sync — aborting")
        return False

    ok = await asyncio.to_thread(_recreate_litellm_blocking)
    if not ok:
        logger.error("heal_anthropic_auth: LiteLLM recreate failed")
        return False

    healthy = await asyncio.to_thread(_wait_for_litellm_blocking)
    logger.info("heal_anthropic_auth: complete, healthy=%s", healthy)
    return healthy
