"""Guards the central tool permission registry against drift.

The key invariant: every `@agent.tool` / `@agent.tool_plain` registered in
`bogi/agent.py` has exactly one entry in `tool_permissions.REGISTRY`, and the
registry has no stale entries. Parsed from source via AST so the test stays
honest even if the module can't be imported in CI (no pydantic_ai install).
"""

from __future__ import annotations

import ast
from pathlib import Path

from bogi.modules import tool_permissions as tp
from bogi.modules.tool_permissions import PermissionClass

AGENT_PY = Path(__file__).resolve().parent.parent / "bogi" / "agent.py"


def _agent_tool_names() -> set[str]:
    """Collect every function decorated with @agent.tool / @agent.tool_plain."""
    tree = ast.parse(AGENT_PY.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # form: agent.tool / agent.tool_plain (no call)
            if (
                isinstance(dec, ast.Attribute)
                and isinstance(dec.value, ast.Name)
                and dec.value.id == "agent"
                and dec.attr in {"tool", "tool_plain"}
            ):
                names.add(node.name)
    return names


def test_every_tool_has_exactly_one_registry_entry():
    tool_names = _agent_tool_names()
    registry_names = set(tp.REGISTRY)

    missing = tool_names - registry_names
    stale = registry_names - tool_names
    assert not missing, f"tools without a permission entry: {sorted(missing)}"
    assert not stale, f"registry entries with no matching tool: {sorted(stale)}"


def test_registry_is_self_consistent():
    for name, entry in tp.REGISTRY.items():
        assert entry.name == name, f"key/name mismatch for {name}"
        assert isinstance(entry.perm_class, PermissionClass)


def test_critical_tools_are_approval_gated():
    # The two agent-initiated gateways must be CRITICAL and flagged as approval-gated.
    for name in ("calendar_delete_event", "request_external_action"):
        assert tp.requires_approval(name), f"{name} must be approval-gated"
        assert tp.REGISTRY[name].perm_class is PermissionClass.CRITICAL


def test_external_writes_are_flagged():
    writers = {
        "calendar_create_event",
        "calendar_quick_add",
        "calendar_update_event",
        "calendar_delete_event",
        "request_external_action",
    }
    for name in writers:
        assert tp.is_external_write(name), f"{name} should be external_write"


def test_read_only_tools_never_write_externally():
    for entry in tp.REGISTRY.values():
        if entry.perm_class is PermissionClass.READ_ONLY:
            assert not entry.external_write, f"{entry.name} is READ_ONLY but external_write"


def test_untrusted_external_sources_flagged():
    # Anything pulling external text must carry untrusted_content + external_system.
    for name in ("gmail_recent", "gmail_search", "gmail_read",
                 "browser_fetch_url", "web_search", "fmi_read_page"):
        entry = tp.REGISTRY[name]
        assert entry.untrusted_content and entry.external_system, name


def test_drafts_are_never_external_writes():
    for name in ("draft_email", "draft_message"):
        entry = tp.REGISTRY[name]
        assert entry.perm_class is PermissionClass.DRAFT
        assert not entry.external_write, f"{name} must never write externally"


def test_high_risk_tools_present():
    # Smoke: the riskiest tools named in the task all have entries.
    for name in ("calendar_create_event", "calendar_quick_add", "calendar_update_event",
                 "calendar_delete_event", "browser_fetch_url", "web_search", "code_run",
                 "memory_forget", "file_save", "file_download", "file_read",
                 "gmail_recent", "fmi_get_courses", "request_external_action"):
        assert tp.get(name) is not None, f"{name} missing from registry"
