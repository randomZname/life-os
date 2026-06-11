# CURRENT REAL STATE — BogiAgent audit (PHASE 0)

**Date:** 2026-05-31 · **HEAD:** `8090618` · **Source of truth:** the real codebase
(this doc), not `V2_REQUIREMENTS.md` / `PLAN.md` (those are inspiration/archives).

This is a no-code audit of what is *actually implemented*. It also folds in the
best ideas absorbed from the now-deleted `jarvis-core` reference project (Lethal
Trifecta matrix, approval-condition table, sanitization rules, cost/cache spans,
sops+age secrets). Those projects were a checklist, not our state.

---

## 1. Architecture as implemented

```
Telegram (python-telegram-bot, long-poll)
  ├─ text / photo (multimodal) / voice (Whisper)
  └─ handlers → BogiAgent.run()
        │
   Pydantic AI agent (bogi/agent.py)         ← the ONLY framework-aware module
        │  @agent.tool / @agent.tool_plain (38 tools)
        │  @agent.system_prompt hooks (memory, skills, date)
        │  wrap_untrusted() around external text
        ▼
   LiteLLM proxy (docker, :4000)             ← model-agnostic; aliases cheap/smart/premium
        │  Claude via Anthropic API (+ OpenAI fallback)
        ▼
   Postgres 16 + pgvector (docker, :5432)    ← state, memory, documents, approvals
   + Google Calendar API  + FMI Moodle (Playwright)  + Obsidian vault (local files)

Process model: watchdog.py (singleton) → spawns `bogi telegram`.
  Self-heal: proactive OAuth refresh (30-min poll) + reactive 401 retry + singleton lock.
Background: Telegram JobQueue — daily brief 08:00, calendar reminders 5min, Moodle watcher 6h.
```

Layering rule (enforced): `bogi/modules/*` is framework-agnostic — no `pydantic_ai`
/ `litellm` imports (one logged exception: `memory.py`, D-006). Only `agent.py`
knows the framework.

## 2. Tools grouped by domain (38)

- **Time/tasks (4):** `get_today_info`, `task_create`, `task_list`, `task_complete`
- **Documents/RAG (4):** `document_ingest`, `document_search`, `document_list`, `document_read`
- **FMI Moodle (8):** `fmi_get_courses`, `fmi_get_materials`, `fmi_download_file`,
  `fmi_read_page`, `fmi_sync_course`, `fmi_get_course_full_info`,
  `fmi_sync_all_courses_info`, `fmi_get_upcoming_deadlines`
- **Drafts/vault (4):** `draft_email`, `draft_message`, `vault_read`, `vault_list`
- **Memory (3):** `memory_save`, `memory_recall`, `memory_forget`
- **Code/files (5):** `code_run`, `file_save`, `file_download`, `file_list`, `file_read`
- **Calendar (6):** `calendar_today`, `calendar_upcoming`, `calendar_search`,
  `calendar_list_calendars`, `calendar_create_event`, `calendar_quick_add`
- **Browser (1):** `browser_fetch_url`
- **Skills (2):** `skill_list`, `skill_read`
- **Approval gate (1):** `request_external_action`

## 3 + 4 + 5. Permission class · external system · approval — per tool

**Classes:** `READ_ONLY` (no writes) · `DRAFT` (writes only to local vault/files,
reversible) · `ACTION` (mutates persistent/local state) · `CRITICAL`
(external write OR destructive/irreversible — *should* be approval-gated).

**Trifecta** (Simon Willison): risk is high when one tool combines private-data
access + untrusted content + external communication. Adapted from jarvis-core's matrix.

| Tool | Class | Touches external | Approval now | Trifecta |
|---|---|---|---|---|
| get_today_info | READ_ONLY | no | no | low |
| task_create / task_complete | ACTION (local DB) | no | no | low |
| task_list | READ_ONLY | no | no | low |
| document_ingest | ACTION (local) | no (reads local file) | no | low |
| document_search / _list / _read | READ_ONLY | no | no | low (returns possibly-untrusted text) |
| fmi_get_courses / _materials / _read_page / _get_course_full_info / _sync_all_courses_info / _get_upcoming_deadlines | READ_ONLY | **yes (Moodle)** | no | **med** (untrusted in) |
| fmi_download_file | ACTION (writes local) | **yes (Moodle)** | no | med |
| fmi_sync_course | ACTION (writes local) | **yes (Moodle)** | no | med |
| draft_email / draft_message | DRAFT (vault/inbox) | no | no | low |
| vault_read / vault_list | READ_ONLY (private) | no | no | low |
| memory_save | ACTION (local DB) | no | no | low |
| memory_recall | READ_ONLY (private) | no | no | low |
| memory_forget | ACTION (destructive, local) | no | **no ⚠** | low |
| code_run | CRITICAL (exec) | no (no network) | **no ⚠** | high-by-nature, sandboxed |
| file_save | DRAFT/ACTION (local) | no | no | low |
| file_download | ACTION (writes local) | **yes (any URL)** | no | med |
| file_list / file_read | READ_ONLY | no | no | low |
| calendar_today / _upcoming / _search / _list_calendars | READ_ONLY (private) | **yes (Google)** | no | low |
| **calendar_create_event** | **CRITICAL (external write)** | **yes (Google)** | **no ⚠** | **high if agent-initiated** |
| **calendar_quick_add** | **CRITICAL (external write)** | **yes (Google)** | **no ⚠** | **high if agent-initiated** |
| browser_fetch_url | READ_ONLY | **yes (allowlist)** | no | med (untrusted in, sanitized) |
| skill_list / skill_read | READ_ONLY (local) | no | no | low |
| request_external_action | ACTION (writes approval row) | no | n/a (is the gate) | low |

**Tools that touch external systems (16):** all 8 FMI + all 6 calendar +
`file_download` + `browser_fetch_url`.
**External *writes* (2 tools + 1 handler path):** `calendar_create_event`,
`calendar_quick_add`, and the Telegram photo+"мудъл" → post-to-Moodle handler
(in `telegram_bot.py`, not an `@agent.tool`).
**Real approval gating today: effectively ZERO.** `request_external_action`
exists and works, but no real writer is routed through it; calendar writes are
allowed because "user-driven" — an assumption that breaks the moment the agent
acts on untrusted inbound content.

> **Update 2026-05-31 (PHASE 2 + follow-up, D-014):** gated writers landed —
> `calendar_update_event` and `calendar_delete_event` → approval → Telegram ✅ →
> `gcal.update_event`/`delete_event` execute (exactly once). System-prompt rule 2б
> forces edits/deletes onto these gated tools (not duplicate-via-quick_add).
> `create_event`/`quick_add` still ungated (user-driven, new events only). Risk #1
> substantially mitigated for calendar; still open for Gmail/Moodle writes.
> `scripts/restart_bot.ps1` added (self-elevating) to make picking up new code easy.

## 6. Background jobs — durability

All via **Telegram JobQueue (in-memory)**: daily brief 08:00, calendar reminders
(5 min, in-memory dedup), Moodle watcher (6 h, diff vs last-seen).
**Not durable:** lost on restart; no retry/checkpoint; no per-job budget.
(jarvis-core's answer was DBOS from day 1 — overkill for us now; see sprint 4.)

## 7. Logging / tracing — current

`cli._setup_logging`: rotating file handler → `bot.log` / `bot.err.log` (gitignored).
`_log_model_used` logs which model answered. **No structured tracing, no spans,
no cost/token/latency capture, no per-tool timing.** We are effectively blind to
what the agent does at runtime. → PHASE 1 (`docs/OBSERVABILITY_PLAN.md`).

## 8. Secrets handling — current

Plain `.env` (gitignored). `.env.example` with placeholders. OAuth tokens under
`data/gcal/` + `~/.claude/.credentials.json` (gitignored). Pre-commit hook blocks
Tier-3 files (`.env`/secrets/`data`/`vault`/keys). **No encrypted-at-rest secrets,
no secret-content scanner, no redaction in logs.** → `docs/SECRETS_POLICY.md`.

## 9. Tests & coverage gaps

47 tests pass (DB tests need Postgres up). Covered: memory v1 (33), approvals (8),
skills (6). **Gaps:** no tests for agent tool wiring, Telegram handlers, gcal
read/write, fmi scraper (network), background jobs, the approval *callback* path,
or end-to-end agent runs. No behavioral/eval harness. No coverage measurement.

## 10. Top 10 risks (current)

1. **External writes without an approval gate** — `calendar_create_event` /
   `calendar_quick_add` (+ Moodle self-post) rely on the "user-driven" assumption;
   Lethal Trifecta is live the moment the agent writes based on Moodle/web content.
2. **Zero observability** — blind to model used, tool calls, latency, errors, cost.
3. **No cost guard** — runaway spend possible; no daily/monthly budget alarm.
4. **`code_run` not approval-gated** — sandboxed (no network) but unbounded use.
5. **Background jobs non-durable** — restart loses scheduled work; no retry/budget.
6. **`memory_forget` / file writes are unaudited & ungated** — silent data loss.
7. ~~**Tool-output sanitization is minimal**~~ — *mitigated (sprint 5a):*
   `bogi/modules/sanitize.py` behind `wrap_untrusted` truncates + neutralizes
   prompt-injection triggers on all untrusted tool output.
8. **Secrets in plain `.env`, no log redaction** — a stray `logger.info(payload)`
   could leak a token; OAuth tokens on disk.
9. **No eval framework** — regressions caught only by unit tests, not behavior.
10. **Moodle auto-post path** posts to an external system on agent-processed image
    content without an approval step.

## 11. Recommended next 5 sprints

1. **Observability (Logfire) + cost/cache tracking** — `OBSERVABILITY_PLAN.md`. P0.
2. **Approval writer end-to-end** — calendar `update`/`delete` behind the gate;
   route `create`/`quick_add` through approval when *agent-initiated*. P0.
3. **Secrets hardening** — `SECRETS_POLICY.md`: scanner + log redaction now;
   sops+age as documented future path. P0.
4. **Durable background jobs + budget guard** — persist schedule/state; per-job
   cost ceiling; daily/monthly spend alarm. P1.
5. **Tool-output sanitization v2 + prompt caching** — truncation + injection
   stripping + provenance tags; Anthropic cache breakpoints for cost. P1.

---

### Ideas absorbed from `jarvis-core` (project since deleted)

- **Lethal Trifecta tool matrix** → §3-5 above (the classification lens).
- **Approval-condition table** → external write / destructive file op / code+network
  / LLM-with-secret-context / bulk >5 items. Adopt as the gate's policy (sprint 2).
- **Tool-output sanitization rules** → strip injection patterns, truncate
  (8k default / 4k web / 2k code-stdout), provenance tags (sprint 5).
- **Cost/cache OTel spans** → `gen_ai.usage.*`, `cache_creation`/`cache_read`
  tokens, `run_id`, `tool_call_id`, `cost_usd` (PHASE 1).
- **Prompt caching** → Anthropic 4-breakpoint policy; track creation *and* read
  tokens (caching can *raise* cost if entries expire unhit) (sprint 5).
- **Secrets: sops + age** → encrypted-at-rest secrets, committed `.enc.yaml`,
  gitignored decrypted `.env` (future path in `SECRETS_POLICY.md`).
- **ADR-per-file** (`decisions/NNNN-*.md`) → optional upgrade over single
  `05_DECISIONS.md` if the log grows.
- **NOT adopted:** FastAPI rewrite, DBOS-from-day-1, CaMeL planner/executor split,
  Codex review-gate — heavier than a solo, already-functional bot needs now.
