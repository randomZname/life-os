"""Central tool permission registry — one source of truth for what each agent
tool is allowed to touch.

Framework-agnostic (no `pydantic_ai` / `litellm` imports), so it sits in
`bogi/modules/`. It is descriptive metadata, not an enforcement layer: every
`@agent.tool` / `@agent.tool_plain` in `bogi/agent.py` has exactly one entry here
(guaranteed by `tests/test_tool_permissions.py`). Use it to reason about the
Lethal Trifecta (D-007) in one place instead of re-reading every wrapper.

Permission classes (rising risk):
  READ_ONLY  — reads only, no state change anywhere.
  DRAFT      — produces a local artifact that is NEVER sent/executed externally
               (email/message drafts in `vault/inbox/`).
  ACTION     — executes a real state change immediately: local DB writes,
               sandboxed code, or USER-DRIVEN external writes that are allowed
               without an approval gate (D-007: calendar create/update/quick_add).
  CRITICAL   — irreversible and/or agent-initiated external write that MUST pass
               the approval queue (`calendar_delete_event`, `request_external_action`).

Flags (independent of the class):
  private_data      — handles Богдан's private data (calendar/gmail/vault/memory).
  untrusted_content — returns/ingests external text wrapped `<untrusted_content>`.
  external_system   — talks to an outside system (Moodle/Google/web/Gmail).
  external_write    — causes a write/effect on an external system.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PermissionClass(str, Enum):
    READ_ONLY = "read_only"
    DRAFT = "draft"
    ACTION = "action"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ToolPermission:
    name: str
    perm_class: PermissionClass
    private_data: bool = False
    untrusted_content: bool = False
    external_system: bool = False
    external_write: bool = False
    note: str = ""


def _p(
    name: str,
    perm_class: PermissionClass,
    *,
    private_data: bool = False,
    untrusted_content: bool = False,
    external_system: bool = False,
    external_write: bool = False,
    note: str = "",
) -> ToolPermission:
    return ToolPermission(
        name=name,
        perm_class=perm_class,
        private_data=private_data,
        untrusted_content=untrusted_content,
        external_system=external_system,
        external_write=external_write,
        note=note,
    )


_RO = PermissionClass.READ_ONLY
_DRAFT = PermissionClass.DRAFT
_ACT = PermissionClass.ACTION
_CRIT = PermissionClass.CRITICAL


_ALL: list[ToolPermission] = [
    # ---- General ----
    _p("get_today_info", _RO),
    _p("task_create", _ACT, note="local DB write"),
    _p("task_list", _RO),
    _p("task_complete", _ACT, note="local DB write"),
    # ---- Documents / RAG ----
    _p("document_ingest", _ACT, untrusted_content=True, note="ingests file text into pgvector"),
    _p("document_search", _RO, untrusted_content=True),
    _p("document_list", _RO),
    _p("document_read", _RO, untrusted_content=True),
    # ---- FMI Moodle (external reads; scraped text is untrusted) ----
    _p("fmi_get_courses", _RO, untrusted_content=True, external_system=True),
    _p("fmi_get_materials", _RO, untrusted_content=True, external_system=True),
    _p("fmi_download_file", _ACT, untrusted_content=True, external_system=True,
       note="downloads file to data/courses/"),
    _p("fmi_read_page", _RO, untrusted_content=True, external_system=True),
    _p("fmi_sync_course", _ACT, untrusted_content=True, external_system=True,
       note="download + ingest combo"),
    _p("fmi_get_course_full_info", _RO, untrusted_content=True, external_system=True),
    _p("fmi_sync_all_courses_info", _RO, untrusted_content=True, external_system=True),
    _p("fmi_get_upcoming_deadlines", _RO, untrusted_content=True, external_system=True),
    # ---- Communication (draft-only, never sent) ----
    _p("draft_email", _DRAFT, private_data=True, note="draft to vault/inbox; never sent"),
    _p("draft_message", _DRAFT, private_data=True, note="draft to vault/inbox; never sent"),
    # ---- Obsidian ----
    _p("vault_read", _RO, private_data=True),
    _p("vault_list", _RO, private_data=True),
    # ---- Long-term memory ----
    _p("memory_save", _ACT, private_data=True, note="persists a long-term fact"),
    _p("memory_recall", _RO, private_data=True),
    _p("memory_forget", _ACT, private_data=True, note="destructive soft-delete of a memory"),
    # ---- Code execution (sandboxed: timeout, no network) ----
    _p("code_run", _ACT, note="runs Python in an isolated subprocess (no network)"),
    # ---- Files (sandboxed in data/files/) ----
    _p("file_save", _ACT, note="local write under data/files/"),
    _p("file_download", _ACT, untrusted_content=True, external_system=True,
       note="fetches URL into data/files/"),
    _p("file_list", _RO),
    _p("file_read", _RO, untrusted_content=True, note="reads possibly external-sourced file"),
    # ---- Google Calendar ----
    _p("calendar_today", _RO, private_data=True, untrusted_content=True, external_system=True),
    _p("calendar_upcoming", _RO, private_data=True, untrusted_content=True, external_system=True),
    _p("calendar_search", _RO, private_data=True, untrusted_content=True, external_system=True),
    _p("calendar_list_calendars", _RO, private_data=True, external_system=True),
    _p("calendar_create_event", _ACT, private_data=True, external_system=True, external_write=True,
       note="user-driven new event; ungated by D-007"),
    _p("calendar_quick_add", _ACT, private_data=True, external_system=True, external_write=True,
       note="user-driven new event; ungated by D-007"),
    # ---- Browser / web ----
    _p("browser_fetch_url", _RO, untrusted_content=True, external_system=True),
    _p("web_search", _RO, untrusted_content=True, external_system=True),
    # ---- Multi-calendar (classified agenda/count) ----
    _p("calendar_agenda", _RO, private_data=True, untrusted_content=True, external_system=True),
    _p("calendar_count", _RO, private_data=True, external_system=True),
    # ---- Gmail (read-only; bodies untrusted) ----
    _p("gmail_recent", _RO, private_data=True, untrusted_content=True, external_system=True),
    _p("gmail_search", _RO, private_data=True, untrusted_content=True, external_system=True),
    _p("gmail_read", _RO, private_data=True, untrusted_content=True, external_system=True),
    _p("gmail_send", _CRIT, private_data=True, external_system=True, external_write=True,
       note="agent-initiated email send; routed through the approval queue"),
    # ---- Skills (progressive disclosure) ----
    _p("skill_list", _RO),
    _p("skill_read", _RO),
    # ---- Approval-gated external actions ----
    _p("request_external_action", _CRIT, external_system=True, external_write=True,
       note="generic gate; never executes directly"),
    _p("calendar_update_event", _ACT, private_data=True, external_system=True, external_write=True,
       note="user-driven edit; executes immediately, ungated by D-014/D-022"),
    _p("calendar_delete_event", _CRIT, private_data=True, external_system=True, external_write=True,
       note="irreversible; routed through the approval queue"),
    # ---- Life-OS: Personal CRM (people) — local private data ----
    _p("person_add", _ACT, private_data=True, note="CRM upsert"),
    _p("person_log", _ACT, private_data=True, note="log an interaction"),
    _p("person_find", _RO, private_data=True),
    _p("people_due", _RO, private_data=True),
    # ---- Life-OS: Money & tutoring — local private data ----
    _p("money_log", _ACT, private_data=True, note="record income/expense"),
    _p("money_report", _RO, private_data=True),
    _p("money_month", _RO, private_data=True),
    # ---- Life-OS: Universal capture — local private data ----
    _p("capture_save", _ACT, private_data=True, note="save to inbox"),
    _p("capture_inbox", _RO, private_data=True),
    _p("capture_search", _RO, private_data=True),
    _p("capture_file", _ACT, private_data=True, note="mark capture filed"),
    # ---- Life-OS: Monitors — local rows; actual fetch happens in a background job ----
    _p("monitor_add", _ACT, private_data=True, note="create a watcher row (fetch is a job)"),
    _p("monitor_list", _RO, private_data=True),
    _p("monitor_remove", _ACT, private_data=True, note="deactivate a watcher"),
    # ---- Life-OS: Habits — local private data ----
    _p("habit_add", _ACT, private_data=True),
    _p("habit_log", _ACT, private_data=True),
    _p("habit_status", _RO, private_data=True),
]


REGISTRY: dict[str, ToolPermission] = {tp.name: tp for tp in _ALL}


def get(name: str) -> ToolPermission | None:
    """Return the permission entry for a tool, or None if unregistered."""
    return REGISTRY.get(name)


def requires_approval(name: str) -> bool:
    """True if the tool is CRITICAL — must pass the approval queue before effect."""
    tp = REGISTRY.get(name)
    return tp is not None and tp.perm_class is PermissionClass.CRITICAL


def is_external_write(name: str) -> bool:
    """True if the tool causes a write/effect on an external system."""
    tp = REGISTRY.get(name)
    return tp is not None and tp.external_write
