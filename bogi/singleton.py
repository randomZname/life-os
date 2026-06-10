"""Cross-platform singleton lock for long-running processes.

Used by watchdog.py and `bogi telegram` to guarantee at most one live
instance. Without this, Telegram returns 409 Conflict on the second
poller and both processes spam tracebacks at each other.

Design:
- Lock file lives in `data/runtime/<name>.lock` (gitignored via `data/`).
- File contents: JSON {"pid": int, "started_at": iso, "cmdline": str}.
- Acquire algorithm:
    1. Try atomic create (mode='x'). On success, lock is ours.
    2. On FileExistsError, read PID and check via psutil.pid_exists.
       - If dead → stale: remove and retry once.
       - If alive → raise AlreadyRunning with details from the file.
- Release: best-effort remove. Owners track their own PID so we only
  delete a lock that still references us (no clobber on race).

Windows + POSIX both supported. No fcntl needed — atomic O_EXCL create
is the cross-platform primitive.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psutil

from bogi.config import settings

logger = logging.getLogger(__name__)


@dataclass
class LockInfo:
    pid: int
    started_at: str
    cmdline: str


class AlreadyRunning(RuntimeError):
    """Raised when a live instance already owns the named lock."""

    def __init__(self, name: str, info: LockInfo):
        self.name = name
        self.info = info
        super().__init__(
            f"singleton '{name}' already owned by PID {info.pid} "
            f"(started {info.started_at}, cmd: {info.cmdline})"
        )


def _runtime_dir() -> Path:
    p = settings.data_path / "runtime"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _lock_path(name: str) -> Path:
    return _runtime_dir() / f"{name}.lock"


def _read_info(path: Path) -> LockInfo | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return LockInfo(
            pid=int(raw.get("pid", 0)),
            started_at=str(raw.get("started_at", "")),
            cmdline=str(raw.get("cmdline", "")),
        )
    except Exception:
        return None


def _write_info(path: Path, info: LockInfo) -> None:
    path.write_text(
        json.dumps(
            {"pid": info.pid, "started_at": info.started_at, "cmdline": info.cmdline}
        ),
        encoding="utf-8",
    )


def _self_info() -> LockInfo:
    cmd = " ".join(sys.argv)[:300]
    return LockInfo(
        pid=os.getpid(),
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        cmdline=cmd,
    )


def acquire(name: str) -> LockInfo:
    """Acquire singleton lock or raise `AlreadyRunning`.

    Caller is responsible for calling `release(name)` (use `lock(name)`
    context manager when possible).
    """
    path = _lock_path(name)
    me = _self_info()

    # Try atomic create.
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = _read_info(path)
        if existing is None:
            # Unparseable — treat as stale.
            logger.warning("singleton %s: unreadable lock file, overwriting", name)
        elif existing.pid == me.pid:
            # Re-acquire by same PID — rare (test re-entry). Just refresh.
            return existing
        elif psutil.pid_exists(existing.pid):
            raise AlreadyRunning(name, existing)
        else:
            logger.info(
                "singleton %s: stale lock from dead PID %d — removing",
                name, existing.pid,
            )
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        # Retry once (no recursion).
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)

    try:
        os.write(fd, json.dumps({
            "pid": me.pid,
            "started_at": me.started_at,
            "cmdline": me.cmdline,
        }).encode("utf-8"))
    finally:
        os.close(fd)

    logger.info("singleton %s acquired by PID %d", name, me.pid)
    return me


def release(name: str) -> None:
    """Release if we still own the lock. Idempotent."""
    path = _lock_path(name)
    info = _read_info(path)
    if info is None:
        return
    if info.pid != os.getpid():
        logger.warning(
            "singleton %s release: file references PID %d, not us (%d) — leaving",
            name, info.pid, os.getpid(),
        )
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    logger.info("singleton %s released", name)


def status(name: str) -> tuple[str, LockInfo | None]:
    """Inspect lock without acquiring.

    Returns (state, info) where state is one of:
      'free'    — no lock file
      'live'    — locked by live process
      'stale'   — lock file references dead PID
      'corrupt' — lock file unreadable
    """
    path = _lock_path(name)
    if not path.exists():
        return "free", None
    info = _read_info(path)
    if info is None:
        return "corrupt", None
    if psutil.pid_exists(info.pid):
        return "live", info
    return "stale", info


class lock:
    """Context manager wrapper around `acquire`/`release`."""

    def __init__(self, name: str):
        self.name = name
        self.info: LockInfo | None = None

    def __enter__(self) -> LockInfo:
        self.info = acquire(self.name)
        return self.info

    def __exit__(self, *exc) -> None:
        release(self.name)


def acquire_or_exit(name: str, *, exit_code: int = 0) -> LockInfo:
    """Common pattern: acquire and exit cleanly if already held.

    `exit_code=0` because "already running" is a deliberate user/system
    state, not a crash. Watchdog will not respawn on 0.
    """
    try:
        return acquire(name)
    except AlreadyRunning as exc:
        # One concise line — never a traceback.
        print(f"[singleton] {exc}", file=sys.stderr)
        logger.warning("singleton %s blocked: %s", name, exc.info)
        sys.exit(exit_code)
