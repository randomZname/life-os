"""Eval runner: drive the real agent over scenarios and report tool choices.

`run_all` makes REAL LLM calls (intended — the maintainer runs it live). The
pure helpers (`extract_tool_calls`, `format_report`) and `run_scenario` are
agent-agnostic, so they can be unit-tested with a fake agent / fake result.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from evals.scenarios import SCENARIOS, Scenario

try:
    from pydantic_ai.messages import ToolCallPart as _ToolCallPart
except Exception:  # pragma: no cover - import shape changed
    _ToolCallPart = None  # type: ignore[assignment,misc]

_OUTPUT_EXCERPT_LEN = 200


def extract_tool_calls(result: Any) -> list[str]:
    """Collect every tool name called during a Pydantic AI run.

    Walks `result.all_messages()`; for each message looks at its `.parts` and
    keeps the ones that are tool calls. A part is a tool call when it is a
    `ToolCallPart` (preferred check) or, defensively, when it carries a
    `tool_name` attribute. Returns names in call order (duplicates kept).
    """
    names: list[str] = []
    messages = getattr(result, "all_messages", None)
    if not callable(messages):
        return names
    for msg in messages() or []:
        for part in getattr(msg, "parts", None) or []:
            # A part is a tool call if it's a real ToolCallPart OR (defensively)
            # carries a `tool_name` attr — the latter also lets tests use a fake.
            is_tool_call = (
                _ToolCallPart is not None and isinstance(part, _ToolCallPart)
            ) or hasattr(part, "tool_name")
            if not is_tool_call:
                continue
            name = getattr(part, "tool_name", None)
            if name:
                names.append(name)
    return names


def _output_text(result: Any) -> str:
    return str(getattr(result, "output", "") or "")


async def run_scenario(agent: Any, scenario: Scenario, *, user_id: int = 0) -> dict:
    """Run one scenario against `agent` and judge pass/fail.

    Passes when every tool in `scenario.expect_tools` appears among the called
    tools AND (no `expect_output_contains`, or the substring is in the output).
    Any exception → passed=False with the error captured.
    """
    from bogi.agent import BogiDeps

    expected = list(scenario.expect_tools)
    try:
        result = await agent.run(
            scenario.prompt,
            deps=BogiDeps(user_id=user_id, current_prompt=scenario.prompt),
        )
        tools = extract_tool_calls(result)
        output = _output_text(result)
        tools_ok = all(t in tools for t in expected)
        substr = scenario.expect_output_contains
        output_ok = substr is None or substr in output
        return {
            "name": scenario.name,
            "passed": tools_ok and output_ok,
            "expected": expected,
            "got_tools": tools,
            "output_excerpt": output[:_OUTPUT_EXCERPT_LEN],
            "error": None,
        }
    except Exception as exc:
        return {
            "name": scenario.name,
            "passed": False,
            "expected": expected,
            "got_tools": [],
            "output_excerpt": "",
            "error": str(exc),
        }


async def run_all(*, user_id: int = 0) -> list[dict]:
    """Build the real agent once and run every scenario. Makes REAL LLM calls."""
    from bogi.agent import build_agent

    agent = build_agent()
    results: list[dict] = []
    for scenario in SCENARIOS:
        results.append(await run_scenario(agent, scenario, user_id=user_id))
    return results


def format_report(results: list[dict]) -> str:
    """Render a readable pass/fail table + 'X/Y passed' summary."""
    lines: list[str] = []
    header = f"{'':2} {'SCENARIO':28} {'EXPECTED':24} GOT"
    lines.append(header)
    lines.append("-" * len(header))
    passed = 0
    for r in results:
        if r.get("passed"):
            passed += 1
        mark = "PASS" if r.get("passed") else "FAIL"
        exp = ",".join(r.get("expected") or []) or "(none)"
        got = ",".join(r.get("got_tools") or []) or "(none)"
        lines.append(f"{mark:4} {r.get('name', '?'):28} {exp:24} {got}")
        if r.get("error"):
            lines.append(f"     error: {r['error']}")
    total = len(results)
    lines.append("-" * len(header))
    lines.append(f"{passed}/{total} passed")
    return "\n".join(lines)


# Convenience for callers that prefer plain dicts over the dataclass.
def scenarios_as_dicts() -> list[dict]:
    return [asdict(s) for s in SCENARIOS]
