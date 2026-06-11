"""Build the static GitHub Pages demo (docs/index.html) from dashboard.html.

Injects a fetch() shim that serves the same fictional data used by
screenshot_dashboard.py, plus a small canned-reply chat engine, so the full
dashboard UI runs client-side with no backend and no real data.
"""

from __future__ import annotations

import json
from pathlib import Path

from screenshot_dashboard import API, CHAT_REPLY

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "bogi" / "web" / "static" / "dashboard.html"
OUT = ROOT / "docs" / "index.html"

REPLIES = [
    [
        "изпит|бази данни|повторя|научи",
        CHAT_REPLY,
    ],
    [
        "план|календар|повторение|събитие|запази",
        "Готово — предлагам 3 сесии за повторение: четвъртък 18:00 (нормални "
        "форми), петък 17:00 (транзакции), неделя 10:00 (индекси). Събитията са "
        "в **approval queue** — потвърди и ги записвам в Google Calendar. ✅",
    ],
    [
        "пари(?![а-я])|разход|приход|бюджет|похарчи",
        "Този месец: **840 лв** приход срещу **412.50 лв** разход → **+427.50 лв** "
        "нето. Най-голям разход: учебник по статистика (45 лв). Приходът е от "
        "7 урока по математика.",
    ],
    [
        "запомни|памет|спомни",
        "Записах го в дългосрочната памет (namespace `study/databases`), с dedup "
        "срещу 218-те съществуващи спомена. Ще го извадя сам, когато стане "
        "релевантно.",
    ],
    [
        "здравей|кой си|какво можеш|помощ",
        "Здравей! Аз съм J.A.R.V.I.S. — личен Life-OS агент с **34 инструмента**: "
        "календар, Moodle крайни срокове, RAG търсене в лекциите, финанси, "
        "навици, хора, монитори и дългосрочна памет. Питай ме нещо от панелите "
        "вдясно.",
    ],
]

DEFAULT_REPLY = (
    "Това е **статично демо** с измислени данни — тук отговарям само на няколко "
    "теми (изпити, план, пари, памет). Истинският агент върти същия UI върху "
    "жив backend: Pydantic AI + Postgres/pgvector + Google Calendar + Moodle. "
    "Кодът е в репото. 🙂"
)

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


def main() -> None:
    html = SRC.read_text(encoding="utf-8")
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
    print(f"built {OUT}")


if __name__ == "__main__":
    main()
