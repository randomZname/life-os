"""Unit tests for the eval runner — FAKE agent only, NO real LLM.

Verifies `extract_tool_calls` and `run_scenario` logic in isolation: a scenario
passes when the expected tool was called, fails when it's missing, the
output-substring check works, and exceptions become passed=False with an error.
"""

from __future__ import annotations

from dataclasses import dataclass

from evals.runner import extract_tool_calls, format_report, run_scenario
from evals.scenarios import Scenario

# --- Fakes mimicking the Pydantic AI run-result shape -----------------------


@dataclass
class _FakePart:
    """Stand-in for ToolCallPart: any object with a `tool_name` attr counts."""

    tool_name: str


@dataclass
class _FakeMessage:
    parts: list


class _FakeResult:
    def __init__(self, tool_names: list[str], output: str) -> None:
        self._messages = [_FakeMessage(parts=[_FakePart(n) for n in tool_names])]
        self.output = output

    def all_messages(self):
        return self._messages


class _FakeAgent:
    """Agent whose .run() ignores the prompt and returns a canned result."""

    def __init__(self, tool_names: list[str], output: str) -> None:
        self._result = _FakeResult(tool_names, output)

    async def run(self, _prompt, *, deps=None, **_kw):
        return self._result


class _BoomAgent:
    async def run(self, _prompt, *, deps=None, **_kw):
        raise RuntimeError("kaboom")


# --- extract_tool_calls -----------------------------------------------------


def test_extract_tool_calls_collects_names_in_order() -> None:
    result = _FakeResult(["calendar_search", "calendar_update_event"], "ok")
    assert extract_tool_calls(result) == ["calendar_search", "calendar_update_event"]


def test_extract_tool_calls_empty_when_no_tools() -> None:
    assert extract_tool_calls(_FakeResult([], "just text")) == []


def test_extract_tool_calls_defensive_on_garbage() -> None:
    assert extract_tool_calls(object()) == []


# --- run_scenario -----------------------------------------------------------


async def test_passes_when_expected_tool_called() -> None:
    agent = _FakeAgent(["calendar_search", "calendar_update_event"], "готово")
    sc = Scenario(name="move", prompt="...", expect_tools=["calendar_update_event"])
    res = await run_scenario(agent, sc)
    assert res["passed"] is True
    assert res["error"] is None
    assert "calendar_update_event" in res["got_tools"]


async def test_fails_when_expected_tool_missing() -> None:
    agent = _FakeAgent(["web_search"], "нещо")
    sc = Scenario(name="move", prompt="...", expect_tools=["calendar_update_event"])
    res = await run_scenario(agent, sc)
    assert res["passed"] is False


async def test_empty_expect_tools_passes_with_no_calls() -> None:
    agent = _FakeAgent([], "Париж")
    sc = Scenario(name="plain", prompt="...", expect_tools=[])
    res = await run_scenario(agent, sc)
    assert res["passed"] is True


async def test_output_substring_check_pass_and_fail() -> None:
    sc = Scenario(
        name="capital",
        prompt="...",
        expect_tools=[],
        expect_output_contains="Париж",
    )
    ok = await run_scenario(_FakeAgent([], "Столицата е Париж."), sc)
    assert ok["passed"] is True

    bad = await run_scenario(_FakeAgent([], "Столицата е Лондон."), sc)
    assert bad["passed"] is False


async def test_substring_fails_even_if_tools_match() -> None:
    sc = Scenario(
        name="both",
        prompt="...",
        expect_tools=["web_search"],
        expect_output_contains="нужен текст",
    )
    res = await run_scenario(_FakeAgent(["web_search"], "друго"), sc)
    assert res["passed"] is False


async def test_exception_path_sets_passed_false_and_error() -> None:
    sc = Scenario(name="boom", prompt="...", expect_tools=["whatever"])
    res = await run_scenario(_BoomAgent(), sc)
    assert res["passed"] is False
    assert res["error"] == "kaboom"
    assert res["got_tools"] == []


# --- format_report ----------------------------------------------------------


def test_format_report_summary_counts() -> None:
    results = [
        {"name": "a", "passed": True, "expected": ["x"], "got_tools": ["x"], "error": None},
        {"name": "b", "passed": False, "expected": ["y"], "got_tools": [], "error": "oops"},
    ]
    report = format_report(results)
    assert "1/2 passed" in report
    assert "PASS" in report and "FAIL" in report
    assert "oops" in report
