"""FMI Moodle scraper using Playwright.

URL се параметризира през settings.fmi_base_url. По подразбиране:
https://learn.fmi.uni-sofia.bg
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from playwright.async_api import Browser, Page, async_playwright

from bogi.config import settings

logger = logging.getLogger(__name__)

# Network errors worth retrying — typically transient (DNS hiccup, VPN restart, brief offline).
_RETRYABLE_NET_ERRORS = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_NETWORK_CHANGED",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_TIMED_OUT",
    "ERR_PROXY_CONNECTION_FAILED",
)


def _is_retryable_net_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(code in msg for code in _RETRYABLE_NET_ERRORS)


async def _goto(page: "Page", url: str, *, retries: int = 3) -> None:
    """Navigate and wait for Moodle 4.x to stabilise.

    Handles:
    - ERR_ABORTED (JS redirects) — wait for domcontentloaded
    - Transient DNS/network errors — exponential backoff retry
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            break
        except Exception as exc:
            if "ERR_ABORTED" in str(exc):
                logger.debug("ERR_ABORTED on %s — waiting for domcontentloaded", url)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=15_000)
                except Exception:
                    pass
                break
            if _is_retryable_net_error(exc) and attempt < retries:
                backoff = 2 ** (attempt - 1) + 0.5 * attempt
                logger.warning(
                    "Transient network error on %s (attempt %d/%d): %s — retrying in %.1fs",
                    url, attempt, retries, exc, backoff,
                )
                last_exc = exc
                await asyncio.sleep(backoff)
                continue
            raise
    else:
        if last_exc is not None:
            raise last_exc

    # Moodle 4.x does background JS navigation after domcontentloaded.
    # Wait for networkidle so evaluate doesn't hit a destroyed context.
    try:
        await page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass


def _is_login_page(url: str) -> bool:
    return "/login/" in url or "login/index.php" in url


def _slugify(text: str, max_len: int = 80) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:max_len] or "course"


class FMIScraper:
    """Playwright-based scraper for FMI Moodle (learn.fmi.uni-sofia.bg)."""

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        base_url: str | None = None,
        data_dir: str | None = None,
    ) -> None:
        self.username = username or settings.fmi_username
        self.password = password or settings.fmi_password
        self.base_url = (base_url or settings.fmi_base_url).rstrip("/")
        self.data_dir = Path(data_dir or settings.courses_path).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = None
        self.browser: Browser | None = None
        self._context = None
        self.page: Page | None = None
        self._logged_in = False

    async def _launch(self) -> None:
        if self.browser:
            return
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self.browser.new_context(accept_downloads=True)
        self.page = await self._context.new_page()

    async def _fresh_page(self) -> "Page":
        """New page in same context — shares login cookies, avoids context destruction.

        Lazily launches the browser if it was never started (e.g. the standalone
        `bogi web` dashboard calls a scrape before any login), so callers never
        hit a None context.
        """
        if self._context is None:
            await self._launch()
        assert self._context is not None
        return await self._context.new_page()

    async def _goto_auth(self, page: "Page", url: str) -> None:
        """Navigate with automatic re-login if session expired."""
        await _goto(page, url)
        if _is_login_page(page.url):
            logger.warning("Session expired — re-logging in")
            self._logged_in = False
            await self.login()
            await _goto(page, url)

    async def login(self) -> bool:
        """Login to FMI Moodle. Returns True on success.

        Resilient to:
        - Transient DNS / network errors (retried via `_goto`)
        - Moodle 4.x selector variations (multiple post-login indicators)
        - Slow networks (waits for navigation explicitly after submit)
        """
        if self._logged_in:
            return True

        if not self.username or not self.password:
            raise ValueError("FMI_USERNAME / FMI_PASSWORD не са зададени в .env")

        await self._launch()
        assert self.page is not None

        for attempt in range(1, 4):
            try:
                await _goto(self.page, f"{self.base_url}/login/index.php")

                # If already logged in, login form may redirect straight to dashboard.
                if not _is_login_page(self.page.url):
                    if await self._login_succeeded():
                        logger.info("FMI Moodle login: already authenticated")
                        self._logged_in = True
                        return True

                try:
                    await self.page.wait_for_selector("input[name='username']", timeout=10_000)
                except Exception:
                    if await self._login_succeeded():
                        logger.info("FMI Moodle login: redirected before form (already in)")
                        self._logged_in = True
                        return True
                    raise

                await self.page.fill("input[name='username']", self.username)
                await self.page.fill("input[name='password']", self.password)

                # Submit and wait for navigation to actually complete before checking DOM.
                async with self.page.expect_navigation(
                    wait_until="domcontentloaded", timeout=30_000
                ):
                    await self.page.click("button[type='submit'], #loginbtn")

                try:
                    await self.page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass

                if await self._login_succeeded():
                    logger.info("FMI Moodle login successful")
                    self._logged_in = True
                    return True

                # If still on login page — likely wrong credentials, do not retry.
                if _is_login_page(self.page.url):
                    err = await self._extract_login_error()
                    logger.error("FMI Moodle login rejected: %s", err or "credentials likely wrong")
                    return False

                logger.error(
                    "FMI Moodle login: no success indicator found on %s", self.page.url
                )
                return False

            except Exception as exc:
                if _is_retryable_net_error(exc) and attempt < 3:
                    backoff = 2 ** attempt
                    logger.warning(
                        "Login attempt %d failed with transient network error: %s — retrying in %ds",
                        attempt, exc, backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                logger.exception("FMI login error: %s", exc)
                return False

        return False

    async def _login_succeeded(self) -> bool:
        """Check multiple Moodle 4.x post-login indicators."""
        assert self.page is not None
        # URL-based: dashboard or any non-login page after submit
        if "/my" in self.page.url and not _is_login_page(self.page.url):
            return True
        # DOM-based — try every known selector for logged-in state.
        selectors = (
            ".usermenu",
            "[data-region='drawer-toggle']",
            "#user-menu-toggle",
            ".userbutton",
            "a[href*='/login/logout.php']",
            "body.userloggedin",
            "nav .dropdown.usermenu",
            "[data-region='user-menu-toggle']",
        )
        for sel in selectors:
            try:
                if await self.page.query_selector(sel):
                    return True
            except Exception:
                continue
        return False

    async def _extract_login_error(self) -> str:
        """Try to read Moodle's inline login error message."""
        assert self.page is not None
        for sel in (".loginerrors", "#loginerrormessage", ".alert-danger", ".error"):
            try:
                el = await self.page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if text:
                        return text[:300]
            except Exception:
                continue
        return ""

    async def get_courses(self) -> list[dict]:
        """Връща списък със записаните курсове."""
        if not self._logged_in:
            await self.login()
        page = await self._fresh_page()
        try:
            await self._goto_auth(page, f"{self.base_url}/my/courses.php")
            raw = await page.eval_on_selector_all(
                'a[href*="course/view.php?id="]',
                """els => els.map(e => ({
                    text: e.innerText.trim(),
                    href: e.href
                })).filter(e => e.text.length > 2)""",
            )
        finally:
            await page.close()

        seen_ids: set[str] = set()
        courses: list[dict] = []
        for link in raw:
            match = re.search(r"id=(\d+)", link["href"])
            if not match:
                continue
            cid = match.group(1)
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            courses.append({"fmi_id": cid, "name": link["text"], "url": link["href"]})
        logger.info("Found %d courses", len(courses))
        return courses

    async def get_materials(self, course_url: str) -> list[dict]:
        """Връща списък с ресурси, задания и тестове за даден курс."""
        if not self._logged_in:
            await self.login()
        page = await self._fresh_page()
        try:
            await self._goto_auth(page, course_url)
            materials: list[dict] = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                document.querySelectorAll('a.aalink, .activityinstance a, .activity a[href*="/mod/"]').forEach(e => {
                    const href = e.href;
                    const text = (e.innerText || '').trim();
                    if (!href || !text || seen.has(href)) return;
                    seen.add(href);

                    // Grab due date / availability from nearby container
                    const container = e.closest('li.activity, .activityli, .activity');
                    let dueDate = '';
                    if (container) {
                        const dateEl = container.querySelector(
                            '.activitydate, .availabilityinfo, [data-region="activity-dates"], ' +
                            '.activity-dates, .conditional-info, .badge'
                        );
                        if (dateEl) dueDate = dateEl.innerText.trim().slice(0, 300);
                    }

                    let kind = 'page';
                    if (href.includes('/mod/resource/')) kind = 'resource';
                    else if (href.includes('/mod/folder/')) kind = 'folder';
                    else if (href.includes('/mod/url/')) kind = 'url';
                    else if (href.includes('/mod/assign/')) kind = 'assignment';
                    else if (href.includes('/mod/quiz/')) kind = 'quiz';
                    else if (href.includes('/mod/forum/')) kind = 'forum';
                    else if (href.includes('/mod/page/')) kind = 'page';

                    results.push({ text, url: href, kind, due_date: dueDate });
                });

                return results;
            }
        """)
        finally:
            await page.close()

        logger.info("Found %d materials in course %s", len(materials), course_url)
        return materials

    async def download_file(self, file_url: str, course_name: str = "uncategorized") -> str:
        """Download a Moodle resource. Returns local file path."""
        if not self._logged_in:
            await self.login()
        assert self.page is not None

        target_dir = self.data_dir / _slugify(course_name)
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with self.page.expect_download(timeout=60_000) as dl_info:
                await self.page.goto(file_url)
            download = await dl_info.value
            suggested = download.suggested_filename or "file"
            target = target_dir / suggested
            await download.save_as(str(target))
            logger.info("Downloaded %s -> %s", file_url, target)
            return str(target)
        except Exception as exc:
            # Сайтът може да не направи download — пробваме requests-style fallback
            logger.warning("Direct download failed for %s: %s. Falling back to fetch.", file_url, exc)
            return await self._fetch_to_file(file_url, target_dir)

    async def _fetch_to_file(self, url: str, target_dir: Path) -> str:
        assert self.page is not None
        response = await self.page.context.request.get(url)
        body = await response.body()
        # Намиране на име на файла
        cd = response.headers.get("content-disposition", "")
        match = re.search(r'filename="?([^";]+)"?', cd)
        filename = match.group(1) if match else url.rstrip("/").split("/")[-1] or "downloaded"
        target = target_dir / filename
        target.write_bytes(body)
        return str(target)

    async def read_page(self, url: str) -> str:
        """Връща текстовото съдържание на Moodle страница."""
        if not self._logged_in:
            await self.login()
        page = await self._fresh_page()
        try:
            await self._goto_auth(page, url)
            text = await page.evaluate(
                "() => document.querySelector('main')?.innerText || document.body.innerText"
            )
        finally:
            await page.close()
        return text or ""

    async def get_full_course_info(self, course_url: str, course_name: str = "") -> dict:
        """Пълна структурирана информация за един курс: задания, тестове, материали.

        Не посещава всяка отделна страница — взима данните директно от страницата на курса.
        """
        materials = await self.get_materials(course_url)

        assignments = [m for m in materials if m["kind"] == "assignment"]
        quizzes = [m for m in materials if m["kind"] == "quiz"]
        resources = [m for m in materials if m["kind"] in {"resource", "folder"}]
        forums = [m for m in materials if m["kind"] == "forum"]

        return {
            "course_name": course_name,
            "course_url": course_url,
            "assignments": assignments,
            "quizzes": quizzes,
            "resources": resources,
            "forums": forums,
            "total_items": len(materials),
        }

    async def sync_all_courses_info(self) -> list[dict]:
        """Пълна информация за ВСИЧКИ записани курсове наведнъж.

        Итерира всеки курс и извлича задания, тестове, материали.
        """
        courses = await self.get_courses()
        results: list[dict] = []
        for course in courses:
            try:
                logger.info("Syncing course info: %s", course["name"])
                info = await self.get_full_course_info(course["url"], course["name"])
                info["fmi_id"] = course["fmi_id"]
                results.append(info)
            except Exception as exc:
                logger.warning("Failed course %s: %s", course["name"], exc)
                results.append({
                    "course_name": course["name"],
                    "fmi_id": course.get("fmi_id", ""),
                    "error": str(exc),
                })
        return results

    async def get_upcoming_events(self) -> list[dict]:
        """Scrape upcoming assignments and quizzes from Moodle calendar.

        Returns list of dicts with keys: title, url, time_text, course, kind.
        kind is one of: 'assignment' | 'quiz' | 'other'
        """
        if not self._logged_in:
            await self.login()
        page = await self._fresh_page()
        try:
            await self._goto_auth(page, f"{self.base_url}/calendar/view.php?view=upcoming")
            events: list[dict] = await page.evaluate(r"""
            () => {
                function parseKind(component, url, title) {
                    component = component || '';
                    if (component.includes('assign') || url.includes('/mod/assign/')) return 'assignment';
                    if (component.includes('quiz') || url.includes('/mod/quiz/')) return 'quiz';
                    if (/тест|quiz|exam|изпит|контролно/i.test(title)) return 'quiz';
                    if (/домашно|задание|задача|assignment|предаване/i.test(title)) return 'assignment';
                    return 'other';
                }

                // Moodle 4.x upcoming view: each REAL event is a div[data-type="event"]
                // with a numeric data-event-id + rich data-* attrs. Date-separator rows
                // (e.g. "събота, 6 юни") have no data-event-id, so this excludes them.
                let evs = Array.from(document.querySelectorAll('[data-type="event"][data-event-id], div.event[data-event-id]'));
                if (evs.length > 0) {
                    return evs.map(ev => {
                        const nameEl = ev.querySelector('h3.name, .name');
                        const title = (ev.getAttribute('data-event-title') || (nameEl ? nameEl.innerText : '')).trim();
                        const link = ev.querySelector('a[href*="/mod/"]');
                        const url = link ? link.href : '';
                        // First .col-11 inside the description body is the time row.
                        const timeEl = ev.querySelector('.description .col-11, .col-11');
                        const time_text = timeEl ? timeEl.innerText.trim().replace(/\s+/g, ' ') : '';
                        const courseEl = ev.querySelector('a[href*="course/view.php"]');
                        const course = courseEl ? courseEl.innerText.trim() : '';
                        return {
                            title,
                            url,
                            time_text,
                            course,
                            kind: parseKind(ev.getAttribute('data-event-component'), url, title),
                        };
                    }).filter(e => e.title.length > 0);
                }

                // Fallback A — "event-list-item" region structure (some 4.x themes).
                let items = Array.from(document.querySelectorAll('[data-region="event-list-item"]'));
                if (items.length > 0) {
                    return items.map(item => {
                        const titleEl = item.querySelector('[data-region="event-name"] a, .event-name a, h5 a, h6 a');
                        const timeEl = item.querySelector('[data-region="event-time-start-date"], time, .text-muted, small');
                        const courseEl = item.querySelector('[data-region="event-course-link"] a, .course-name a, .course-name');
                        const url = titleEl ? titleEl.href : '';
                        const title = titleEl ? titleEl.innerText.trim() : '';
                        return {
                            title,
                            url,
                            time_text: timeEl ? timeEl.innerText.trim() : '',
                            course: courseEl ? courseEl.innerText.trim() : '',
                            kind: parseKind('', url, title),
                        };
                    }).filter(e => e.title.length > 0 && !/view=day/.test(e.url));
                }

                // Fallback B — legacy Moodle 3.x.
                items = Array.from(document.querySelectorAll('.event, li[class*="event"]'));
                return items.map(e => {
                    const a = e.querySelector('a[href*="/mod/"]') || e.querySelector('a');
                    const timeEl = e.querySelector('.date, .event-time, time, small');
                    const courseEl = e.querySelector('.course, .event-course');
                    const url = a ? a.href : '';
                    const title = a ? a.innerText.trim() : e.innerText.slice(0, 120).trim();
                    return {
                        title,
                        url,
                        time_text: timeEl ? timeEl.innerText.trim() : '',
                        course: courseEl ? courseEl.innerText.trim() : '',
                        kind: parseKind('', url, title),
                    };
                }).filter(e => e.title.length > 0 && !/view=day/.test(e.url));
            }
        """)
        finally:
            await page.close()

        logger.info("Found %d upcoming events in Moodle calendar", len(events))
        return events

    async def send_self_message(self, text: str) -> str | None:
        """Send a message into the personal Moodle self-chat. Returns new message_id or None."""
        if not self._logged_in:
            await self.login()
        page = await self._fresh_page()
        try:
            await self._goto_auth(page, f"{self.base_url}/message/index.php")
            await page.wait_for_function(
                "document.querySelectorAll('[data-conversation-id]').length > 0",
                timeout=20_000,
            )
            await page.click("[data-conversation-id]")
            await page.wait_for_selector(
                "textarea[data-region='send-message-txt'], textarea.form-control",
                timeout=10_000,
            )

            textarea = await page.query_selector("textarea[data-region='send-message-txt']")
            if not textarea:
                textarea = await page.query_selector("textarea.form-control")
            if not textarea:
                logger.warning("send_self_message: textarea not found")
                return None

            await textarea.click()
            await textarea.fill(text)

            send_btn = await page.query_selector(
                "[data-region='footer-container'] button[data-action='send-message'], "
                "[data-region='message-drawer'] button[data-action='send-message']"
            )
            if send_btn:
                await send_btn.click()
            else:
                await page.keyboard.press("Control+Enter")

            # Wait for DOM to settle after send
            await page.wait_for_timeout(2500)

            # Read back latest msg_id (this is our own message now)
            new_id = await page.evaluate("""() => {
                const msgs = Array.from(document.querySelectorAll('[data-region="message"]'));
                if (!msgs.length) return '';
                return msgs[msgs.length - 1].getAttribute('data-message-id') || '';
            }""")
            return new_id or None
        finally:
            await page.close()

    async def get_latest_inbox_message(self) -> dict | None:
        """Връща последното съобщение от Moodle личния self-чат (Богдан → Богдан).

        Returns dict с keys: message_id, content, time. Returns None ако няма съобщения.
        """
        if not self._logged_in:
            await self.login()
        page = await self._fresh_page()
        try:
            await self._goto_auth(page, f"{self.base_url}/message/index.php")
            try:
                await page.wait_for_function(
                    "document.querySelectorAll('[data-conversation-id]').length > 0",
                    timeout=20_000,
                )
            except Exception:
                return None

            await page.click("[data-conversation-id]")
            try:
                await page.wait_for_selector("[data-region=message]", timeout=10_000)
            except Exception:
                return None

            msg = await page.evaluate("""() => {
                const msgs = Array.from(document.querySelectorAll('[data-region="message"]'));
                if (!msgs.length) return null;
                const last = msgs[msgs.length - 1];
                return {
                    message_id: last.getAttribute('data-message-id') || '',
                    content: last.innerText?.trim() || '',
                    time: (last.querySelector('time') || last)?.getAttribute('datetime') || ''
                };
            }""")
            return msg
        finally:
            await page.close()

    async def close(self) -> None:
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._logged_in = False
