"""Canned behavioral eval scenarios.

Each scenario is a realistic Bulgarian prompt the user might type, plus the tool
name(s) that MUST appear in the agent's tool calls. `expect_tools` is treated as
"must include" — the agent may legitimately call extra tools first (e.g.
`calendar_search` before `calendar_update_event`, or `get_today_info` before a
calendar write). Event ids / texts are placeholders; these check behavior, not
real data.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scenario:
    """One behavioral eval case."""

    name: str
    prompt: str
    # Tool names that MUST appear among the agent's tool calls (subset check).
    expect_tools: list[str] = field(default_factory=list)
    # Optional substring that MUST appear in the final output.
    expect_output_contains: str | None = None


SCENARIOS: list[Scenario] = [
    Scenario(
        name="calendar_move_event",
        prompt="Премести събитието с id evt_demo_123 за 15:00 днес.",
        expect_tools=["calendar_update_event"],
    ),
    Scenario(
        name="web_search",
        prompt="Потърси в интернет кога е следващото частично слънчево затъмнение.",
        expect_tools=["web_search"],
    ),
    Scenario(
        name="memory_recall",
        prompt="Какво си запомнил за мен досега?",
        expect_tools=["memory_recall"],
    ),
    Scenario(
        name="fmi_upcoming_deadlines",
        prompt="Какви предстоящи задачи имам в Moodle?",
        expect_tools=["fmi_get_upcoming_deadlines"],
    ),
    Scenario(
        name="today_info",
        prompt="Коя дата е днес и колко е часът?",
        expect_tools=["get_today_info"],
    ),
    Scenario(
        name="plain_factual_no_tool",
        prompt="Каква е столицата на Франция? Отговори с една дума.",
        expect_tools=[],
        expect_output_contains="Париж",
    ),
]
