"""Build the static GitHub Pages demo (docs/index.html) from dashboard.html.

Takes the real dashboard UI, translates the user-facing strings to English
(the product itself speaks Bulgarian to its user), and injects a fetch() shim
that serves fictional demo data plus a small canned-reply chat engine — so the
full dashboard runs client-side with no backend and no real data.

The README screenshot is taken from this build (scripts/screenshot_dashboard.py).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "bogi" / "web" / "static" / "dashboard.html"
OUT = ROOT / "docs" / "index.html"

BRIEF = (
    "Good morning! ☀️ Today you have a **Databases lecture at 09:00** and a "
    "tutoring session at 14:00. ⏰ Homework 3 (Databases) is due tomorrow "
    "23:59 — the BCNF theory part is still left. 🔥 12-day study streak — "
    "keep it going."
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
            {"start": "2026-06-11T09:00", "summary": "Lecture — Databases", "event_class": "university"},
            {"start": "2026-06-11T14:00", "summary": "Math tutoring session", "event_class": "lesson"},
            {"start": "2026-06-11T19:00", "summary": "Gym", "event_class": "personal"},
        ],
        "deadlines": [
            {"kind": "assignment", "title": "Homework 3 — normal forms", "course": "Databases", "time_text": "tomorrow, 23:59"},
            {"kind": "quiz", "title": "Quiz — Java collections", "course": "OOP practicum", "time_text": "in 3 days"},
        ],
    },
    "/api/money": {
        "month": {"currency": "BGN", "income_total": 840.0, "expense_total": 412.5, "net": 427.5},
        "recent": [
            {"amount": 60, "kind": "income", "description": "Tutoring — math"},
            {"amount": -45, "kind": "expense", "description": "Statistics textbook"},
            {"amount": -18.9, "kind": "expense", "description": "Lunch"},
        ],
    },
    "/api/habits": {
        "habits": [
            {"name": "Study 2h", "streak": 12, "last7": [True, True, True, True, False, True, True]},
            {"name": "Gym", "streak": 4, "last7": [False, True, False, True, True, False, True]},
            {"name": "Reading", "streak": 7, "last7": [True, True, True, True, True, True, True]},
        ],
    },
    "/api/people": {
        "birthdays": [{"name": "Martin", "in_days": 2}],
        "stale": [{"name": "Victor", "days_since": 21}],
    },
    "/api/monitors": {
        "monitors": [
            {"name": "GPU price — RTX 4070", "last_value": "1099 BGN", "last_checked_at": "2026-06-11T08:30:00"},
            {"name": "Exam session schedule", "last_value": "no change", "last_checked_at": "2026-06-11T07:00:00"},
        ],
    },
    "/api/captures": {
        "inbox": [
            {"kind": "idea", "content": "Compare pgvector vs Qdrant for RAG"},
            {"kind": "task", "content": "Register for the Statistics exam"},
            {"kind": "url", "content": "fastapi.tiangolo.com/advanced"},
        ],
    },
    "/api/brief": {"text": BRIEF},
}

# [keyword-regex (EN + BG), reply] — first match wins.
REPLIES = [
    [
        "exam|databases?|revise|изпит|бази данни",
        "The **Databases** exam is on June 24, 10:00. Based on the indexed "
        "lecture notes I'd revise: normal forms (lecture 7), transactions & "
        "ACID (lecture 9), indexes & B-trees (lecture 10). Homework 3 covers "
        "the same material — start there. Want me to schedule revision "
        "sessions in your calendar?",
    ],
    [
        "plan|calendar|schedule|revision|план|календар|повторение",
        "Done — proposing 3 revision sessions: Thu 18:00 (normal forms), "
        "Fri 17:00 (transactions & ACID), Sun 10:00 (indexes). They're "
        "waiting in the **approval queue** — confirm and I'll write them to "
        "Google Calendar. ✅",
    ],
    [
        "money|earn|spen[dt]|income|expense|budget|пари(?![а-я])|разход|приход|бюджет",
        "This month: **840 BGN** income vs **412.50 BGN** expenses → "
        "**+427.50 BGN** net. Biggest expense: statistics textbook (45 BGN). "
        "The income comes from 7 math tutoring sessions.",
    ],
    [
        "remember|memor(y|ies)|запомни|памет",
        "Saved to long-term memory (namespace `study/databases`), deduplicated "
        "against the 218 existing memories. I'll surface it on my own when it "
        "becomes relevant.",
    ],
    [
        "today|agenda|днес",
        "Today: Databases lecture at 09:00 🎓, math tutoring at 14:00 and gym "
        "at 19:00. One deadline on the radar: **Homework 3 (Databases)** due "
        "tomorrow 23:59.",
    ],
    [
        "habit|streak|навици",
        "Habit check: **Study 2h** — 12-day streak 🔥, **Reading** — 7 days, "
        "**Gym** — 4 days (missed the day before yesterday). Today is still "
        "open for all three.",
    ],
    [
        "reach out|birthday|who should|потърся|рожден",
        "Martin's birthday is in 2 days 🎂 — worth planning something. And "
        "you haven't talked to Victor in 21 days; a quick message wouldn't "
        "hurt. 📞",
    ],
    [
        "inbox",
        "3 items in the capture inbox: 💡 an idea (compare pgvector vs Qdrant "
        "for RAG), ✅ a task (register for the Statistics exam) and 🔗 a link "
        "(FastAPI advanced docs). Want me to turn the task into a calendar "
        "event?",
    ],
    [
        "hello|\\bhi\\b|who are you|what can|здравей|кой си|какво можеш",
        "Hi! I'm J.A.R.V.I.S. — a personal Life-OS agent with **34 tools**: "
        "calendar, Moodle deadlines, RAG search over lecture notes, finances, "
        "habits, people follow-ups, web monitors and long-term memory. Ask me "
        "about anything you see in the panels.",
    ],
]

DEFAULT_REPLY = (
    "This is a **static demo** with fictional data — I only answer a few "
    "topics here (exams, plans, money, habits, memory, inbox). The real agent "
    "runs this same UI on a live backend: Pydantic AI + Postgres/pgvector + "
    "Google Calendar + Moodle. The code is in the repo. 🙂"
)

# Ordered: longer strings first so substrings don't clobber them.
TRANSLATIONS = [
    ("Какво имам днес?", "What do I have today?"),
    ("Колко уроци изкарах този месец?", "How much did I earn this month?"),
    ("Кого да потърся?", "Who should I reach out to?"),
    ("Покажи навиците", "Show my habits"),
    ("Какво има в inbox-а?", "What's in my inbox?"),
    ("Кажи на Джарвис...", "Talk to Jarvis..."),
    ("зареждане…", "loading…"),
    ("⚠️ Празен отговор.", "⚠️ Empty reply."),
    ("Връзката с агента се разпадна.", "Lost connection to the agent."),
    ("грешка: ", "error: "),
    ("няма връзка", "no connection"),
    ("календарът иска re-auth", "calendar needs re-auth"),
    ("Moodle недостъпен", "Moodle unreachable"),
    ("няма събития днес", "no events today"),
    ("КРАЙНИ СРОКОВЕ", "DEADLINES"),
    ("няма крайни срокове", "no deadlines"),
    ("няма транзакции", "no transactions"),
    ("няма навици", "no habits"),
    ("няма монитори", "no monitors"),
    ("няма данни", "no data"),
    ("inbox е празен", "inbox is empty"),
    ("(без име)", "(untitled)"),
    ("(навик)", "(habit)"),
    ("(монитор)", "(monitor)"),
    ("потърси ", "reach out to "),
    (">приход<", ">income<"),
    (">разход<", ">expense<"),
    (">нето<", ">net<"),
    ("'днес'", "'today'"),
    ("'утре'", "'tomorrow'"),
    ("'след '+days+'д'", "'in '+days+'d'"),
    ("+'д'", "+'d'"),
    ("НАВИЦИ", "HABITS"),
    ("МОНИТОРИ", "MONITORS"),
    ("'НАВ'", "'HAB'"),
    ("ХОРА", "PEOPLE"),
    ("ДНЕС", "TODAY"),
    ("ПАРИ", "MONEY"),
]

SHIM = """<script>
/* DEMO MODE — static GitHub Pages build. All data below is fictional. */
(function(){
  const DATA = __DATA__;
  const REPLIES = __REPLIES__.map(([re, text]) => [new RegExp(re, 'i'), text]);
  const DEFAULT_REPLY = __DEFAULT_REPLY__;
  const json = obj => new Response(JSON.stringify(obj), {status:200, headers:{'Content-Type':'application/json'}});
  const realFetch = window.fetch.bind(window);
  window.fetch = function(path, opts){
    const p = String(path);
    if(p.indexOf('/api/chat') !== -1){
      let text = '';
      try{ text = JSON.parse((opts||{}).body).text || ''; }catch(e){}
      const hit = REPLIES.find(([re]) => re.test(text));
      const reply = hit ? hit[1] : DEFAULT_REPLY;
      return new Promise(res => setTimeout(() => res(json({reply})), 700 + Math.random()*600));
    }
    for(const key in DATA){
      if(p.indexOf(key) !== -1) return Promise.resolve(json(DATA[key]));
    }
    return realFetch(path, opts);
  };
})();
</script>
<script>"""


def build() -> Path:
    html = SRC.read_text(encoding="utf-8")
    for src, dst in TRANSLATIONS:
        html = html.replace(src, dst)
    shim = (
        SHIM
        .replace("__DATA__", json.dumps(API, ensure_ascii=False))
        .replace("__REPLIES__", json.dumps(REPLIES, ensure_ascii=False))
        .replace("__DEFAULT_REPLY__", json.dumps(DEFAULT_REPLY, ensure_ascii=False))
    )
    assert html.count('<script>\n"use strict";') == 1
    html = html.replace('<script>\n"use strict";', shim + '\n"use strict";', 1)
    html = html.replace(
        "<footer>BOGIAGENT · J.A.R.V.I.S. HUD ·",
        "<footer>J.A.R.V.I.S. · LIVE DEMO — fictional data ·",
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    print(f"built {build()}")
