"""Behavioral eval harness for BogiAgent.

Runs the agent on canned scenarios and asserts WHICH tool it chose (and
optionally an output substring). Catches behavioral regressions that unit tests
miss (e.g. tz / memory-hybrid silent bugs).

NOT part of the default pytest suite (`testpaths = ["tests"]`). `run_all` makes
REAL LLM calls — the lead/maintainer runs it live via the future `bogi eval`.
"""

from evals.runner import extract_tool_calls, format_report, run_all, run_scenario
from evals.scenarios import SCENARIOS

__all__ = [
    "SCENARIOS",
    "extract_tool_calls",
    "format_report",
    "run_all",
    "run_scenario",
]
