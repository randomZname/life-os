# OBSERVABILITY PLAN — Logfire (PHASE 1)

**Date:** 2026-05-31 · Goal: the smallest useful tracing layer so one Telegram
request, one background job, and one approval can be inspected end-to-end —
model + tools + durations + errors + cost — with **no secrets in the data**.

## Why Logfire (decision)

Logfire is by Pydantic, same authors as Pydantic AI. It gives **near-free
auto-instrumentation** of our agent: `logfire.instrument_pydantic_ai()` traces
every agent run, model call, and tool call as OpenTelemetry spans. That makes
PHASE 1 small — we add config + a few manual spans, not a framework.

**Privacy stance (decided):** Logfire **cloud** + **aggressive scrubbing**.
Best visibility for least effort; scrubbing keeps tokens/bodies out. If scrubbing
ever proves leaky, fall back to local-only OTel export. Data leaving the machine
is acceptable *only* with the scrub guarantees below.

## What to instrument

Auto (via `instrument_pydantic_ai()` + `instrument_httpx()`):
- agent run started / finished
- model call via LiteLLM (httpx span → model, tokens, latency)
- tool call started / finished (per `@agent.tool`)

Manual spans (add explicitly):
- Telegram request received (text/photo/voice) → root span, `source=telegram`
- RAG search (`document_search`) — query shape, k, hit count (not raw text)
- memory save / recall / forget — namespace + count (not content)
- approval request created
- approval decision approved / rejected
- background job started / finished / failed (daily brief, reminders, Moodle watcher)
- exceptions / errors (attach `error_type`, stack — scrubbed)

## Span attributes (where applicable)

Adopted from jarvis-core's cost/cache spec, mapped to OTel `gen_ai.*`:

| Attribute | Notes |
|---|---|
| `bogi.run_id` | UUID per agent run (root) |
| `bogi.source` | `telegram` / `cli` / `background` |
| `bogi.tool_name` | tool child spans only |
| `bogi.tool_call_id` | UUID per tool invocation |
| `bogi.permission_class` | READ_ONLY / DRAFT / ACTION / CRITICAL (from audit §3) |
| `bogi.approval_id` | when a tool routes through the gate |
| `gen_ai.request.model` | resolved model from LiteLLM metadata |
| `gen_ai.usage.input_tokens` / `output_tokens` | per request |
| `gen_ai.usage.cache_creation.input_tokens` | Anthropic `cache_creation_input_tokens` |
| `gen_ai.usage.cache_read.input_tokens` | Anthropic `cache_read_input_tokens` |
| `bogi.cost_usd` | from tokens × pricing, or LiteLLM `LiteLLM_SpendLogs` |
| `duration_ms`, `success`, `error_type` | every span |

Track **both** cache token types: entries created but expired-before-hit *raise*
cost — read-only tracking hides that.

## Security rules (the scrub guarantee)

Never send to Logfire (or any log): API keys, OAuth tokens, cookies, passwords,
full private email/message bodies, raw secrets. Implementation:
- enable Logfire's built-in scrubbing (default redacts common secret patterns);
- add a project scrub callback: deny field names `authorization|api_key|token|
  cookie|password|secret`; redact value patterns (`sk-ant-`, `sk-`, `ya29.`,
  PEM, `ghp_`);
- log *shapes* not *contents*: query length + hit count, not the query/text;
  namespace + count, not memory bodies.

## Acceptance criteria

- All 47 existing tests still pass.
- One Telegram request shows: model + tool calls + per-step durations + any error.
- One background-job execution is inspectable (start/finish/fail).
- One approval request + decision is inspectable.
- No secret value or private body appears anywhere in the traces.

## Smallest first slice (proposed)

1. `bogi/observability.py` — `configure_logfire()` (token from env; if absent,
   no-op so dev without Logfire still runs), scrub callback, `instrument_*` calls.
2. Call it once at startup (`telegram_bot` / `cli`).
3. One manual root span at Telegram receive with `run_id` + `source`.
4. Manual spans on approval create/decide + background jobs.
5. Verify the four acceptance checks, then commit.
