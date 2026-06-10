"""Browser fetch — read-only, allowlist-protected, sanitized."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Comment

from bogi.modules.sanitize import sanitize

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 50_000

# Тагове, които почти никога не носят основния текст — нав/скриптове/бойлърплейт.
_BOILERPLATE_TAGS = (
    "script",
    "style",
    "iframe",
    "noscript",
    "header",
    "footer",
    "nav",
    "aside",
    "form",
    "svg",
    "button",
    "template",
)

# Колапсва 3+ празни реда до един празен ред (запазва четима абзацна структура).
_BLANKLINES_RE = re.compile(r"\n\s*\n\s*\n+")
# Колапсва множество интервали/табове в рамките на ред до един интервал.
_INLINE_WS_RE = re.compile(r"[ \t\f\v]+")

# По подразбиране permissive — конкретни ограничения се дефинират по skill/tool.
DEFAULT_ALLOWED_DOMAINS: set[str] = {
    "learn.fmi.uni-sofia.bg",
    "fmi.uni-sofia.bg",
    "uni-sofia.bg",
    "moodle.uni-sofia.bg",
    "wikipedia.org",
    "en.wikipedia.org",
    "bg.wikipedia.org",
    "github.com",
    "stackoverflow.com",
    "docs.python.org",
}


def _is_allowed(url: str, allowlist: set[str]) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return any(host == d or host.endswith("." + d) for d in allowlist)


def _extract_text(html: str) -> str:
    """Pure extraction helper — HTML → чист, четим основен текст.

    Без мрежа, без cap, без injection-неутрализация (това е работа на
    ``sanitize`` в ``browser_fetch``). Стъпки:
    - премахва nav/script/style/footer и друг бойлърплейт,
    - предпочита ``<main>``/``<article>`` тялото, ако е налично,
    - колапсва излишните whitespace и празни редове за четимост.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(list(_BOILERPLATE_TAGS)):
        tag.decompose()

    # HTML коментари не носят съдържание за четене.
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    # Ако страницата има ясно основно тяло, използваме него — реже остатъчните
    # менюта/банери, които не са в горните тагове.
    root = soup.find("main") or soup.find("article") or soup.body or soup

    text = root.get_text(separator="\n", strip=True)

    # Нормализиране на whitespace ред по ред, после колапс на празните редове.
    lines = [_INLINE_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip()


async def browser_fetch(
    url: str,
    allowed_domains: set[str] | None = None,
    timeout_s: float = 15.0,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict:
    """Read-only fetch на URL. Връща sanitized четим текст.

    Sanitization:
    - стрипва nav/script/style/footer и друг бойлърплейт, колапсва whitespace
    - cap до ``max_chars`` знака (по подразбиране 50K)
    - неутрализира prompt-injection тригери чрез ``sanitize``
    - резултатът трябва да се обвие в <untrusted_content> от агента
    """
    allow = allowed_domains or DEFAULT_ALLOWED_DOMAINS
    if not _is_allowed(url, allow):
        return {
            "ok": False,
            "error": f"Domain не е в allowlist-а. Позволени: {sorted(allow)}",
        }

    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "BogiAgent/0.1"})
            response.raise_for_status()
        raw = _extract_text(response.text)
        truncated = len(raw) > max_chars
        text = sanitize(raw, max_chars=max_chars)
        return {
            "ok": True,
            "url": url,
            "status": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "text": text,
            "truncated": truncated,
        }
    except httpx.HTTPError as exc:
        logger.warning("browser_fetch failed for %s: %s", url, exc)
        return {"ok": False, "error": f"HTTP error: {exc}"}
