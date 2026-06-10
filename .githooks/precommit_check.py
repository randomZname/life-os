#!/usr/bin/env python3
"""Pre-commit guard for BogiAgent.

Three checks, all hard-blocking:
  1. No forbidden (Tier-3) files staged — secrets / .env / data/ / vault/ / local
     settings / key & token files. (Most are gitignored; this catches force-adds.)
  2. No secret values pasted into staged diffs — content scanner over ADDED lines
     for known-prefix tokens (sk-ant-, sk-, ghp_, xoxb-, ya29., AKIA..., PEM).
  3. Context pack is not drifted — runs .claude/project-context/check_context.py.

Install once per clone:  git config core.hooksPath .githooks
Bypass (use sparingly):  git commit --no-verify

No third-party deps. See 09_AUTONOMY.md (Tier-3), SECRETS_POLICY.md and
07_VALIDATION.md.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (regex on the staged repo-relative path, human label)
FORBIDDEN = [
    (r"(^|/)\.env$", ".env file"),
    (r"(^|/)\.env\.(?!example$)", ".env.* file"),
    (r"^data/", "data/ (user content / tokens)"),
    (r"^vault/(?!README\.md$|\.gitkeep$)", "vault/ (user content)"),
    (r"settings\.local\.json$", "local settings"),
    (r"(^|/)client_secret.*\.json$", "OAuth client secret"),
    (r"(^|/)credentials?.*\.json$", "credentials file"),
    (r"\.pem$", "PEM / private key"),
    (r"\.key$", "key file"),
    (r"token.*\.json$", "token file"),
]


def staged_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        cwd=ROOT, text=True,
    )
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


# --- Secret content scanner -------------------------------------------------
# SHARED SECRET PATTERNS — team contract (label -> compiled regex). Each pattern
# requires a long run of REAL alphanumerics after a known prefix; the regex
# literals below are written with a "[" bracket-class right after the prefix, so
# the source of this file cannot self-match (see VERIFY note in the sprint task).
SECRET_PATTERNS = [
    ("Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_-]{24,}")),
    ("OpenAI/generic key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("Google OAuth token", re.compile(r"ya29\.[A-Za-z0-9_-]{20,}")),
    ("GitHub PAT", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

# Files allowed to contain example secret patterns without blocking.
_ALLOWED_EXACT = {".env.example"}
_ALLOWED_PREFIXES = ("docs/", "tests/")


def _is_allowed_path(path: str) -> bool:
    """True if findings in this repo-relative path should be ignored."""
    norm = path.replace("\\", "/")
    if norm.startswith("./"):
        norm = norm[2:]
    if norm in _ALLOWED_EXACT or norm.rsplit("/", 1)[-1] in _ALLOWED_EXACT:
        return True
    return any(norm.startswith(pfx) for pfx in _ALLOWED_PREFIXES)


def find_secrets_in_diff(diff: str) -> list[str]:
    """Pure helper: scan a `git diff --unified=0` text for secrets in ADDED lines.

    Returns findings like "path: possible <label>". Allowed paths
    (.env.example, docs/, tests/) are skipped. Importable without running git.
    """
    findings: list[str] = []
    current = "<unknown>"
    for line in diff.splitlines():
        if line.startswith("+++ "):
            # "+++ b/path" or "+++ /dev/null"
            target = line[4:].strip()
            if target.startswith("b/"):
                target = target[2:]
            current = target
            continue
        if line.startswith("---") or line.startswith("diff "):
            continue
        if not line.startswith("+"):
            continue  # only ADDED lines (and skip +++ handled above)
        if _is_allowed_path(current):
            continue
        added = line[1:]
        for label, rx in SECRET_PATTERNS:
            if rx.search(added):
                findings.append(f"{current}: possible {label}")
    return findings


def scan_staged_secrets() -> list[str]:
    """Scan staged content for secrets. No-ops cleanly on an empty diff."""
    try:
        diff = subprocess.check_output(
            ["git", "diff", "--cached", "--unified=0"],
            cwd=ROOT, text=True, errors="replace",
        )
    except subprocess.CalledProcessError:
        return []
    if not diff.strip():
        return []
    return find_secrets_in_diff(diff)


def main() -> int:
    problems = []
    for f in staged_files():
        for pat, label in FORBIDDEN:
            if re.search(pat, f):
                problems.append(f"{f}  ({label})")
                break

    if problems:
        print("COMMIT BLOCKED - forbidden (Tier-3) files staged:")
        for p in problems:
            print(f"  [X] {p}")
        print("Unstage them (git restore --staged <file>).")
        print("If this is truly intended, bypass with: git commit --no-verify")
        return 1

    secrets = scan_staged_secrets()
    if secrets:
        print("COMMIT BLOCKED - possible secret value in staged content:")
        for s in secrets:
            print(f"  [X] {s}")
        print("Remove the secret (use .env / .env.example placeholders).")
        print("If this is truly a false positive, bypass with: git commit --no-verify")
        return 1

    checker = ROOT / ".claude" / "project-context" / "check_context.py"
    if checker.exists():
        rc = subprocess.call([sys.executable, str(checker)], cwd=ROOT)
        if rc != 0:
            print("COMMIT BLOCKED - context pack drift (see above).")
            print("Fix the drift, or bypass with: git commit --no-verify")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
