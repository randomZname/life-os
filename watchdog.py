"""Watchdog — keeps BogiAgent alive.

Two loops:
1. Main: respawns the Telegram bot on crash (10s back-off).
2. Background thread: every 30 min refreshes the Claude OAuth token from
   `credentials.json`; if `.env` changed, recreates the LiteLLM container so
   the proxy picks up the new key. Prevents 401 mid-session when tokens
   expire (typical lifetime ~8h, refreshed faster by Claude Code CLI).

Self-heal philosophy: never sit on a stale auth. If anything goes wrong,
log it and keep trying — process exit is the absolute last resort.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [watchdog] %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
DOCKER_BIN = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"

RESTART_DELAY = 10            # seconds between bot restarts
TOKEN_REFRESH_INTERVAL = 30 * 60   # 30 min — well under typical OAuth TTL


def _read_anthropic_key() -> str:
    if not ENV_PATH.exists():
        return ""
    text = ENV_PATH.read_text(encoding="utf-8")
    m = re.search(r"^ANTHROPIC_API_KEY=(.*)$", text, flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


def _recreate_litellm() -> None:
    """Force-recreate the LiteLLM container so it picks up the new .env."""
    try:
        result = subprocess.run(
            [DOCKER_BIN, "compose", "up", "-d", "--force-recreate", "litellm"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            log.info("LiteLLM container recreated with fresh token")
        else:
            log.warning("LiteLLM recreate non-zero exit: %s | %s", result.returncode, result.stderr[:500])
    except Exception as exc:
        log.warning("LiteLLM recreate failed: %s", exc)


def _sync_token_once() -> None:
    """Run sync_oauth_token.sync() defensively."""
    try:
        import sync_oauth_token
        sync_oauth_token.sync()
    except SystemExit:
        log.warning("Token sync exited — keeping current .env value")
    except Exception as exc:
        log.warning("Token sync failed: %s — keeping current .env value", exc)


def _token_refresher_loop(last_token_box: list[str]) -> None:
    """Background loop: refresh token periodically, restart LiteLLM on change."""
    while True:
        time.sleep(TOKEN_REFRESH_INTERVAL)
        log.info("Token refresher: checking credentials.json")
        _sync_token_once()
        current = _read_anthropic_key()
        if current and current != last_token_box[0]:
            log.info("Token changed — recreating LiteLLM")
            _recreate_litellm()
            last_token_box[0] = current
        else:
            log.info("Token unchanged — skip LiteLLM recreate")


def _back_off_seconds(recent_exits: list[float]) -> int:
    """Decide how long to sleep between respawns.

    If the bot has died 3+ times in the last 60s, assume something is
    persistently wrong (Telegram 409 from another machine, crashed
    dependency, etc.) and back off to 60s. Otherwise default 10s.
    """
    now = time.time()
    recent = [t for t in recent_exits if now - t < 60]
    if len(recent) >= 3:
        return 60
    return RESTART_DELAY


def main() -> None:
    # Singleton: refuse to run a second watchdog. Common cause: leftover
    # background process the user forgot about. We exit 0 so any wrapper
    # script doesn't treat this as a crash.
    from bogi.singleton import acquire_or_exit, release

    acquire_or_exit("bogi_watchdog")
    try:
        log.info("Watchdog started. Monitoring bogi telegram bot...")
        log.info("PLAYWRIGHT_BROWSERS_PATH=%s", os.environ.get("PLAYWRIGHT_BROWSERS_PATH"))
        log.info("USERPROFILE=%s", os.environ.get("USERPROFILE"))
        # Mutable box so background thread + main share the latest known token
        last_token_box = [_read_anthropic_key()]
        refresher = threading.Thread(
            target=_token_refresher_loop,
            args=(last_token_box,),
            daemon=True,
            name="oauth-refresher",
        )
        refresher.start()

        recent_exits: list[float] = []
        while True:
            _sync_token_once()
            new_tok = _read_anthropic_key()
            if new_tok and new_tok != last_token_box[0]:
                log.info("Token differs from last known at startup — recreating LiteLLM")
                _recreate_litellm()
                last_token_box[0] = new_tok

            log.info("Starting bot...")
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "bogi", "telegram"], cwd=str(ROOT)
                )
                proc.wait()
                log.warning("Bot exited with code %s.", proc.returncode)
                recent_exits.append(time.time())
            except Exception as exc:
                log.exception("Bot supervisor failed: %s — sleeping then retry", exc)
                recent_exits.append(time.time())
            delay = _back_off_seconds(recent_exits)
            if delay > RESTART_DELAY:
                log.warning("Multiple rapid exits — backing off %ds", delay)
            else:
                log.info("Restarting in %ds...", delay)
            time.sleep(delay)
    finally:
        release("bogi_watchdog")


if __name__ == "__main__":
    main()
