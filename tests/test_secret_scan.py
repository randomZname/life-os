"""Unit tests for the pre-commit staged-content secret scanner.

Tests the pure helper `find_secrets_in_diff` directly (no git needed). Fake keys
are built by concatenation at runtime so this source carries no literal key.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_MODULE_PATH = ROOT / ".githooks" / "precommit_check.py"

_spec = importlib.util.spec_from_file_location("precommit_check", _MODULE_PATH)
assert _spec and _spec.loader
precommit_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(precommit_check)

find_secrets_in_diff = precommit_check.find_secrets_in_diff


def _diff(path: str, *added_lines: str) -> str:
    """Build a minimal unified=0 diff hunk for one file with added lines."""
    head = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
    body = "".join(f"+{ln}\n" for ln in added_lines)
    return head + body


def test_anthropic_key_flagged():
    fake = "sk-ant-" + "A" * 30  # realistic shape, not a real key
    findings = find_secrets_in_diff(_diff("bogi/config.py", f'KEY = "{fake}"'))
    assert findings == ["bogi/config.py: possible Anthropic key"]


def test_generic_openai_key_flagged():
    fake = "sk-" + "B" * 25
    findings = find_secrets_in_diff(_diff("app.py", f"token={fake}"))
    assert any("possible OpenAI/generic key" in f for f in findings)


def test_github_pat_flagged():
    fake = "ghp_" + "c" * 36
    findings = find_secrets_in_diff(_diff("ci.py", fake))
    assert findings == ["ci.py: possible GitHub PAT"]


def test_slack_token_flagged():
    fake = "xoxb-" + "1234567890-abcXYZ"
    findings = find_secrets_in_diff(_diff("notify.py", fake))
    assert any("possible Slack token" in f for f in findings)


def test_aws_key_flagged():
    fake = "AKIA" + "ABCDEFGHIJKLMNOP"
    findings = find_secrets_in_diff(_diff("infra.py", fake))
    assert findings == ["infra.py: possible AWS access key"]


def test_google_token_flagged():
    fake = "ya29." + "A" * 25
    findings = find_secrets_in_diff(_diff("gcal.py", fake))
    assert findings == ["gcal.py: possible Google OAuth token"]


def test_private_key_block_flagged():
    findings = find_secrets_in_diff(
        _diff("id.py", "-----BEGIN RSA PRIVATE KEY-----")
    )
    assert findings == ["id.py: possible Private key block"]


def test_normal_code_line_not_flagged():
    diff = _diff(
        "bogi/agent.py",
        "def handle(message):",
        "    return message.strip().lower()",
        "client = httpx.Client(timeout=30)",
    )
    assert find_secrets_in_diff(diff) == []


def test_env_example_path_skipped():
    fake = "sk-ant-" + "A" * 30
    diff = _diff(".env.example", f"ANTHROPIC_API_KEY={fake}")
    assert find_secrets_in_diff(diff) == []


def test_docs_path_skipped():
    fake = "ghp_" + "c" * 36
    diff = _diff("docs/SECRETS_POLICY.md", f"example: {fake}")
    assert find_secrets_in_diff(diff) == []


def test_tests_path_skipped():
    fake = "sk-" + "B" * 25
    diff = _diff("tests/test_secret_scan.py", f'fake = "{fake}"')
    assert find_secrets_in_diff(diff) == []


def test_removed_lines_not_flagged():
    fake = "sk-ant-" + "A" * 30
    # A removed line (leading '-') must NOT trip the scanner.
    diff = (
        f"diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        f'-KEY = "{fake}"\n'
    )
    assert find_secrets_in_diff(diff) == []


def test_empty_diff_no_op():
    assert find_secrets_in_diff("") == []
