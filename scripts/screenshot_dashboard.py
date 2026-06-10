"""Screenshot the dashboard UI with mocked API data (no backend, no real data).

Serves bogi/web/static/dashboard.html via Playwright route interception and
fulfills every /api/* call with fictional demo JSON, then drives a short chat
and captures docs/img/dashboard.png. Used to refresh the README screenshot.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "bogi" / "web" / "static" / "dashboard.html").read_text(encoding="utf-8")
OUT = ROOT / "docs" / "img"

BRIEF = (
    "Добро утро! ☀️ Днес имаш **лекция по Бази данни в 09:00** и урок в 14:00. "
    "⏰ Домашно 3 по Бази данни е до утре 23:59 — остава ти теорията за BCNF. "
    "🔥 12-дневна серия учене — дръж темпото."
)

CHAT_REPLY = (
    "Изпитът по **Бази данни** е на 24 юни, 10:00. По материалите от индекса "
    "препоръчвам да повториш: нормални форми (лекция 7), транзакции и ACID "
    "(лекция 9), индекси и B-дървета (лекция 10). Домашно 3 покрива същия "
    "материал — започни от него. Да ти направя ли план за повторение в календара?"
)

API = {
    "/api/status": {
        "ok": True,
        "tools": 34,
        "counts": {
            "memories": 218, "people": 12, "captures": 3, "transactions": 57,
            "monitors": 2, "habits": 3, "documents": 142,
        },
    },
    "/api/today": {
        "agenda": [
            {"start": "2026-06-11T09:00", "summary": "Лекция — Бази данни", "event_class": "university"},
            {"start": "2026-06-11T14:00", "summary": "Урок по математика", "event_class": "lesson"},
            {"start": "2026-06-11T19:00", "summary": "Фитнес", "event_class": "personal"},
        ],
        "deadlines": [
            {"kind": "assignment", "title": "Домашно 3 — нормални форми", "course": "Бази данни", "time_text": "утре, 23:59"},
            {"kind": "quiz", "title": "Тест — Java колекции", "course": "ООП практикум", "time_text": "след 3 дни"},
        ],
    },
    "/api/money": {
        "month": {"currency": "лв", "income_total": 840.0, "expense_total": 412.5, "net": 427.5},
        "recent": [
            {"amount": 60, "kind": "income", "description": "Урок — математика"},
            {"amount": -45, "kind": "expense", "description": "Учебник по статистика"},
            {"amount": -18.9, "kind": "expense", "description": "Обяд"},
        ],
    },
    "/api/habits": {
        "habits": [
            {"name": "Учене 2ч", "streak": 12, "last7": [True, True, True, True, False, True, True]},
            {"name": "Фитнес", "streak": 4, "last7": [False, True, False, True, True, False, True]},
            {"name": "Четене", "streak": 7, "last7": [True, True, True, True, True, True, True]},
        ],
    },
    "/api/people": {
        "birthdays": [{"name": "Мартин", "in_days": 2}],
        "stale": [{"name": "Виктор", "days_since": 21}],
    },
    "/api/monitors": {
        "monitors": [
            {"name": "GPU цена — RTX 4070", "last_value": "1099 лв", "last_checked_at": "2026-06-11T08:30:00"},
            {"name": "Сесия — график", "last_value": "без промяна", "last_checked_at": "2026-06-11T07:00:00"},
        ],
    },
    "/api/captures": {
        "inbox": [
            {"kind": "idea", "content": "Сравни pgvector срещу Qdrant за RAG"},
            {"kind": "task", "content": "Запиши се за изпита по статистика"},
            {"kind": "url", "content": "fastapi.tiangolo.com/advanced"},
        ],
    },
    "/api/brief": {"text": BRIEF},
}


def handle(route):
    url = route.request.url
    path = "/" + url.split("/", 3)[-1]
    if path == "/api/chat":
        route.fulfill(json=({"reply": CHAT_REPLY}))
        return
    for key, payload in API.items():
        if path.startswith(key):
            route.fulfill(json=payload)
            return
    route.fulfill(status=200, content_type="text/html; charset=utf-8", body=HTML)


def wait_idle(page):
    page.wait_for_function(
        "document.getElementById('statusTag').textContent === 'AWAITING COMMAND'",
        timeout=60000,
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1720, "height": 1040}, device_scale_factor=2)
        page.route("**/*", handle)
        page.goto("http://bogi.local/")
        page.wait_for_selector("#boot.done", timeout=30000)
        wait_idle(page)  # daily brief finished typing
        page.fill("#input", "Кога е изпитът по Бази данни и какво да повторя?")
        page.keyboard.press("Enter")
        wait_idle(page)  # chat reply finished typing
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "dashboard.png"))
        browser.close()
    print(f"saved {OUT / 'dashboard.png'}")


if __name__ == "__main__":
    main()
