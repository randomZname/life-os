"""Web JSON API tests (Phase 5, CONTRACT 3 — Track A).

Builds a minimal Starlette app from ``api_routes()`` with a fake shared agent on
``app.state.agent`` and monkeypatched external module funcs (``calendars.agenda``)
so NO network / LLM is hit. Asserts each endpoint returns HTTP 200 and the
CONTRACT-3 keys/structure (not exact values — DB-backed counts and life-os reads
may be empty in this env, which is fine).

The DB IS reachable here, so ``/api/status`` counts actually run.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from bogi.modules import calendars
from bogi.web.api import api_routes

# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class _FakeFMI:
    async def get_upcoming_events(self) -> list[dict]:
        return [
            {
                "title": "Домашно 1",
                "course": "Бази данни",
                "time_text": "до петък",
                "kind": "assignment",
            }
        ]


class _FakeAgent:
    """Stand-in for BogiAgent: canned chat reply + a fake .fmi."""

    def __init__(self) -> None:
        self.fmi = _FakeFMI()

    async def run(self, text: str, *, user_id: int, channel: str) -> str:
        return f"ехо: {text}"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # No network: stub the calendar aggregation (gcal/iOS) used by /api/today.
    async def _fake_agenda(*args, **kwargs) -> list[dict]:
        return [
            {
                "summary": "Урок",
                "start": "2026-06-07T10:00:00+03:00",
                "end": "2026-06-07T11:00:00+03:00",
                "owner": "bogdan",
                "cal_type": "work",
                "event_class": "lesson",
                "calendar": "Work",
            }
        ]

    monkeypatch.setattr(calendars, "agenda", _fake_agenda)

    app = Starlette(routes=api_routes())
    app.state.agent = _FakeAgent()
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_chat(client: TestClient) -> None:
    r = client.post("/api/chat", json={"text": "здрасти"})
    assert r.status_code == 200
    data = r.json()
    assert "reply" in data
    assert data["reply"] == "ехо: здрасти"
    assert "ts" in data


def test_chat_empty_text(client: TestClient) -> None:
    r = client.post("/api/chat", json={"text": "   "})
    assert r.status_code == 200
    assert "error" in r.json()


def test_status(client: TestClient) -> None:
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    # DB is reachable; if it ever isn't, the handler returns {"error": ...}.
    if "error" in data:
        pytest.skip(f"status DB error in this env: {data['error']}")
    assert data["ok"] is True
    assert isinstance(data["tools"], int) and data["tools"] > 0
    assert "ts" in data
    counts = data["counts"]
    for key in (
        "memories",
        "people",
        "captures",
        "transactions",
        "monitors",
        "habits",
        "documents",
    ):
        assert key in counts
        assert isinstance(counts[key], int)


def test_today(client: TestClient) -> None:
    r = client.get("/api/today")
    assert r.status_code == 200
    data = r.json()
    assert "agenda" in data
    assert "deadlines" in data
    assert "agenda_error" in data
    assert "deadlines_error" in data
    assert isinstance(data["agenda"], list)
    assert isinstance(data["deadlines"], list)
    # fmi fake returned one deadline with the contract keys.
    d = data["deadlines"][0]
    for key in ("title", "course", "time_text", "kind"):
        assert key in d


def test_today_deadlines_degrade_on_fmi_error(client: TestClient) -> None:
    """A dead Playwright/Moodle session must NOT nuke the whole today panel:
    agenda still renders, deadlines is [] + deadlines_error is set, status 200."""

    class _BoomFMI:
        async def get_upcoming_events(self) -> list[dict]:
            raise RuntimeError("browser context is None")

    client.app.state.agent.fmi = _BoomFMI()
    r = client.get("/api/today")
    assert r.status_code == 200
    data = r.json()
    assert "error" not in data  # whole panel survived
    assert data["deadlines"] == []
    assert data["deadlines_error"] and "browser context is None" in data["deadlines_error"]
    # agenda (independent source) still rendered
    assert isinstance(data["agenda"], list) and data["agenda"]


def test_money(client: TestClient) -> None:
    r = client.get("/api/money")
    assert r.status_code == 200
    data = r.json()
    if "error" in data:
        pytest.skip(f"money DB error in this env: {data['error']}")
    assert "month" in data
    assert "recent" in data
    assert isinstance(data["recent"], list)


def test_people(client: TestClient) -> None:
    r = client.get("/api/people")
    assert r.status_code == 200
    data = r.json()
    if "error" in data:
        pytest.skip(f"people DB error in this env: {data['error']}")
    assert "stale" in data
    assert "birthdays" in data


def test_habits(client: TestClient) -> None:
    r = client.get("/api/habits")
    assert r.status_code == 200
    data = r.json()
    if "error" in data:
        pytest.skip(f"habits DB error in this env: {data['error']}")
    assert "habits" in data
    assert isinstance(data["habits"], list)


def test_captures(client: TestClient) -> None:
    r = client.get("/api/captures")
    assert r.status_code == 200
    data = r.json()
    if "error" in data:
        pytest.skip(f"captures DB error in this env: {data['error']}")
    assert "inbox" in data
    assert isinstance(data["inbox"], list)


def test_monitors(client: TestClient) -> None:
    r = client.get("/api/monitors")
    assert r.status_code == 200
    data = r.json()
    if "error" in data:
        pytest.skip(f"monitors DB error in this env: {data['error']}")
    assert "monitors" in data
    assert isinstance(data["monitors"], list)


def test_brief(client: TestClient) -> None:
    r = client.get("/api/brief")
    assert r.status_code == 200
    data = r.json()
    # compose_brief never raises; text is always present.
    assert "text" in data
    assert isinstance(data["text"], str)
