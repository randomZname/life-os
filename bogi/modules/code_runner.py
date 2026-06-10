"""Sandboxed Python execution for Programming Coach Mode.

Runs short code snippets in a subprocess with a hard timeout. NOT a security
sandbox — strictly for personal-use learning. Disallowed: networking, file ops
outside a tmp dir. On Windows we rely on subprocess isolation + timeout; we do
not attempt full namespace/network isolation here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 10
MAX_OUTPUT_CHARS = 8000


# Block obviously dangerous imports/calls at a textual level. This is a hint
# for the agent's coach mode, not a security boundary.
_FORBIDDEN_SUBSTRINGS = (
    "import socket",
    "import urllib",
    "import requests",
    "import http",
    "from socket",
    "from urllib",
    "from requests",
    "from http",
    "subprocess.",
    "os.system",
    "os.popen",
    "shutil.rmtree",
    "__import__('os')",
    "__import__(\"os\")",
)


def _looks_dangerous(code: str) -> str | None:
    lower = code.lower()
    for needle in _FORBIDDEN_SUBSTRINGS:
        if needle in lower:
            return needle
    return None


async def run_python(code: str, stdin: str = "", timeout: float = DEFAULT_TIMEOUT_SEC) -> dict:
    """Execute `code` in a fresh `python -I` subprocess.

    Returns: {ok, stdout, stderr, returncode, timed_out, blocked_pattern?}.
    `-I` = isolated mode (ignore PYTHON* env vars, no user site, no implicit cwd).
    """
    bad = _looks_dangerous(code)
    if bad:
        return {
            "ok": False,
            "blocked_pattern": bad,
            "stdout": "",
            "stderr": f"Blocked by coach-mode policy: pattern '{bad}' is not allowed in practice sandbox.",
            "returncode": -1,
            "timed_out": False,
        }

    with tempfile.TemporaryDirectory(prefix="bogi_run_") as tmpdir:
        script = Path(tmpdir) / "user.py"
        script.write_text(code, encoding="utf-8")

        env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "TEMP": tmpdir,
            "TMP": tmpdir,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-u", str(script),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=tmpdir,
            )
        except Exception as exc:
            return {"ok": False, "stdout": "", "stderr": f"spawn failed: {exc}", "returncode": -1, "timed_out": False}

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=stdin.encode("utf-8") if stdin else None),
                timeout=timeout,
            )
            timed_out = False
        except asyncio.TimeoutError:
            proc.kill()
            try:
                stdout_b, stderr_b = await proc.communicate()
            except Exception:
                stdout_b, stderr_b = b"", b""
            timed_out = True

        stdout = stdout_b.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
        stderr = stderr_b.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
        rc = proc.returncode if proc.returncode is not None else -1

        return {
            "ok": (rc == 0) and not timed_out,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": rc,
            "timed_out": timed_out,
        }
