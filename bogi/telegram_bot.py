"""Telegram bot — single user entrypoint to BogiAgent.

Allowlist enforced на всеки message. Long-running calls се обработват
асинхронно с edit на статус message-а (Telegram изисква <10s response).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bogi import observability as obs
from bogi.agent import BogiAgent
from bogi.config import settings

logger = logging.getLogger(__name__)


_agent: BogiAgent | None = None

# monitor state: user_id -> {url, last_content}
_monitor_state: dict[int, dict] = {}

# Approval ids already surfaced as an inline card this process — avoids
# re-posting the same card on every later message.
_carded_approvals: set[int] = set()


async def _push_pending_cards(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    """After an agent run, surface any NEW pending approvals as inline ✅/❌ cards.

    The agent tool only records the approval + returns text; without this the
    user would see "⏳ чака одобрение" but no button (had to type /approvals).
    """
    try:
        from bogi.modules import approvals

        pending = await approvals.list_pending(user_id)
    except Exception:
        logger.exception("Failed to list pending approvals for cards")
        return
    for a in pending:
        if a["id"] in _carded_approvals:
            continue
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏳ Заявка #{a['id']}\n{a['preview']}",
                reply_markup=_approval_keyboard(a["id"]),
            )
            _carded_approvals.add(a["id"])
        except Exception:
            logger.exception("Failed to push approval card %s", a["id"])


def _get_agent() -> BogiAgent:
    global _agent
    if _agent is None:
        _agent = BogiAgent()
    return _agent


def _is_allowed(user_id: int) -> bool:
    allowed = settings.allowed_user_ids
    if not allowed:
        logger.warning("TELEGRAM_ALLOWED_USER_IDS не е попълнен — никой няма достъп.")
        return False
    return user_id in allowed


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп до този бот.")
        return
    await update.message.reply_text(
        f"Здрасти, {user.first_name}. Аз съм Боги.\n"
        "Питай ме за курсове, материали или ме помоли да напиша чернова на имейл."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Списък с всички налични команди."""
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп до този бот.")
        return
    text = (
        "🤖 *BogiAgent команди*\n\n"
        "*Чат:*\n"
        "Просто ми пиши свободно. Помня контекста на разговора (sliding window + summary).\n\n"
        "*Снимки:*\n"
        "Прати ми снимка (с/без caption) — анализирам какво има на нея и отговарям. "
        "Caption „мудъл\"/„moodle\" → отговорът отива в твоя Moodle self-chat.\n\n"
        "*Гласови:*\n"
        "Прати ми гласово съобщение — транскрибирам с Whisper и обработвам като текст.\n\n"
        "*Файлове:*\n"
        "Мога да пазя текст в `data/files/` и да свалям URL-и там. Кажи ми "
        "„запази този код във file.py\" или „свали тази картинка от https://...\".\n\n"
        "*Команди:*\n"
        "/start — поздрав и кратко представяне.\n"
        "/help — този списък.\n"
        "/id — показва твоя Telegram user ID (за allowlist).\n"
        "/new — архивира текущия разговор и започва нов (забравям контекста, "
        "но НЕ дългосрочната памет).\n"
        "/upcoming — показва предстоящите домашни и тестове от Moodle, структурирано по дата.\n"
        "/practice <условие> — изрично извикване на programming mode. Може и без командата — "
        "просто прати задачата си и ще получиш пълно решение + изпълнени тестове + анализ.\n"
        "/remember <текст> — запомни нещо дългосрочно (преживява /new и рестарт).\n"
        "/memories — покажи всичко запомнено.\n"
        "/forget <id|описание> — забрави memory.\n"
        "/monitor — стартира мониторинг на личния ти self-chat в Moodle (всеки 10с). "
        "Когато си пишеш ново съобщение там, аз отговарям директно В Moodle, не в Telegram.\n"
        "/monitor\\_stop — спира Moodle мониторинга.\n\n"
        "*Автоматично:*\n"
        "• Дневна сводка в 08:00 — предстоящи задания.\n"
        "• Календарни напомняния ~30 мин преди всяко Google Calendar събитие.\n"
        "• Moodle watcher на 6h — alert когато се появи ново задание или тест.\n"
        "• Watchdog рестартира бота при сривове.\n"
        "• OAuth token се refresh-ва автоматично всеки 30 мин.\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Помага на Богдан да намери своя user ID за allowlist-а."""
    user = update.effective_user
    if user is None:
        return
    await update.message.reply_text(f"Твоят Telegram user ID: `{user.id}`", parse_mode="Markdown")


async def cmd_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показва предстоящите домашни и тестове от Moodle."""
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп до този бот.")
        return

    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(chat_id=chat_id, text="Проверявам Moodle...")

    async def _keep_typing() -> None:
        while True:
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    typing_task = asyncio.create_task(_keep_typing())
    try:
        agent = _get_agent()
        response = await agent.run(
            "Провери предстоящите ми домашни и тестове в Moodle и ги покажи структурирано по дата.",
            user_id=user.id,
            channel="telegram",
        )
    except Exception as exc:
        logger.exception("Upcoming deadlines check failed")
        response = f"Грешка: {exc}"
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    chunks = [response[i : i + 3800] for i in range(0, len(response), 3800)] or ["(няма предстоящи задачи)"]
    await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=chunks[0])
    for extra in chunks[1:]:
        await context.bot.send_message(chat_id=chat_id, text=extra)


def _wants_moodle_routing(caption: str) -> bool:
    """Detect routing keyword in caption (case-insensitive, BG + EN)."""
    if not caption:
        return False
    low = caption.lower().strip()
    triggers = ("мудъл", "moodle", "в мудъл", "to moodle", "→мудъл")
    return any(t in low for t in triggers)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработва снимки (с/без caption) — мултимодален вход за агента.

    Caption съдържа „мудъл"/„moodle" → отговорът отива в Moodle self-chat (не Telegram).
    """
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп до този бот.")
        return

    photo = update.message.photo[-1] if update.message.photo else None
    if photo is None:
        return

    raw_caption = (update.message.caption or "").strip()
    route_to_moodle = _wants_moodle_routing(raw_caption)

    if not raw_caption:
        caption_for_agent = "Опиши какво виждаш на снимката и помогни ако има въпрос."
    elif route_to_moodle:
        caption_for_agent = (
            "На снимката има задача. Реши я ПЪЛНО и ВЯРНО по правилата от Programming Mode "
            "(идея → код → изпълнени тестове → сложност). Готово за предаване. "
            "Не пиши, че решението ще се прати в Moodle — просто дай решението."
        )
    else:
        caption_for_agent = raw_caption

    chat_id = update.effective_chat.id
    status_text = "Решавам и пращам в Moodle..." if route_to_moodle else "Анализирам снимката..."
    status_msg = await context.bot.send_message(chat_id=chat_id, text=status_text)

    async def _keep_typing() -> None:
        while True:
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                await asyncio.sleep(4)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    typing_task = asyncio.create_task(_keep_typing())
    try:
        tg_file = await context.bot.get_file(photo.file_id)
        raw = bytes(await tg_file.download_as_bytearray())
        agent = _get_agent()
        with obs.span(
            "telegram.photo", source="telegram", user_id=user.id, route_to_moodle=route_to_moodle
        ):
            response = await agent.run(
                caption_for_agent,
                user_id=user.id,
                channel="moodle_self" if route_to_moodle else "telegram",
                images=[(raw, "image/jpeg")],
            )
    except Exception as exc:
        logger.exception("Photo handling failed")
        response = f"Грешка: {exc}"
        route_to_moodle = False  # Send error to Telegram, not Moodle
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    if route_to_moodle:
        try:
            chunks = [response[i:i + 3500] for i in range(0, len(response), 3500)]
            for chunk in chunks:
                await agent.fmi.send_self_message(chunk)
            preview = (response[:300] + "…") if len(response) > 300 else response
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=f"✅ Решението пратено в Moodle.\n\n_Преглед:_\n{preview}",
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.exception("Failed to send solution to Moodle")
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=f"❌ Не успях да пратя в Moodle: {exc}\n\nРешение:\n{response[:3500]}",
            )
        return

    chunks = [response[i:i + 3800] for i in range(0, len(response), 3800)] or ["(празен отговор)"]
    await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=chunks[0])
    for extra in chunks[1:]:
        await context.bot.send_message(chat_id=chat_id, text=extra)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Telegram voice message → Whisper transcript → agent → reply."""
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп до този бот.")
        return

    voice = update.message.voice or update.message.audio
    if voice is None:
        return

    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(chat_id=chat_id, text="🎙️ Преписвам гласовото...")

    try:
        from bogi.modules import voice as voice_mod

        tg_file = await context.bot.get_file(voice.file_id)
        raw = bytes(await tg_file.download_as_bytearray())
        suffix = "ogg" if (update.message.voice is not None) else "m4a"
        transcript = await voice_mod.transcribe(raw, filename=f"voice.{suffix}")
    except Exception as exc:
        logger.exception("Voice transcription failed")
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=status_msg.message_id,
            text=f"❌ Не успях да транскрибирам: {exc}",
        )
        return

    if not transcript:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=status_msg.message_id,
            text="(не разпознах нищо в гласовото)",
        )
        return

    await context.bot.edit_message_text(
        chat_id=chat_id, message_id=status_msg.message_id,
        text=f"🎙️ _{transcript}_\n\nОбработвам...", parse_mode="Markdown",
    )

    async def _keep_typing() -> None:
        while True:
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    typing_task = asyncio.create_task(_keep_typing())
    try:
        agent = _get_agent()
        with obs.span("telegram.voice", source="telegram", user_id=user.id):
            response = await agent.run(transcript, user_id=user.id, channel="telegram")
    except Exception as exc:
        logger.exception("Agent run on voice transcript failed")
        response = f"Грешка: {exc}"
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    chunks = [response[i : i + 3800] for i in range(0, len(response), 3800)] or ["(празен отговор)"]
    await context.bot.send_message(chat_id=chat_id, text=chunks[0])
    for extra in chunks[1:]:
        await context.bot.send_message(chat_id=chat_id, text=extra)
    await _push_pending_cards(context, chat_id, user.id)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп до този бот.")
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(chat_id=chat_id, text="Обработвам...")

    # Keep typing indicator alive
    async def _keep_typing() -> None:
        while True:
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    typing_task = asyncio.create_task(_keep_typing())

    try:
        agent = _get_agent()
        with obs.span("telegram.message", source="telegram", user_id=user.id):
            response = await agent.run(text, user_id=user.id, channel="telegram")
    except Exception as exc:
        logger.exception("Agent run failed")
        response = f"Грешка: {exc}"
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    # Telegram message limit ~4000 chars
    chunks = [response[i : i + 3800] for i in range(0, len(response), 3800)] or ["(празен отговор)"]
    await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=chunks[0])
    for extra in chunks[1:]:
        await context.bot.send_message(chat_id=chat_id, text=extra)
    await _push_pending_cards(context, chat_id, user.id)


async def cmd_practice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Practice mode: пълно решение + код + тестове. Само за упражнения, НЕ за оценявани тестове."""
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп.")
        return
    problem = " ".join(context.args).strip() if context.args else ""
    if not problem:
        await update.message.reply_text(
            "Употреба: /practice <условие>\n\n"
            "Пример: /practice Намери най-дългата нарастваща подредица в масив от int.\n\n"
            "Тази команда изрично влиза в programming mode. Можеш и без нея — просто прати "
            "задачата си нормално и ще получиш пълно решение."
        )
        return

    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(chat_id=chat_id, text="Practice mode — мисля...")

    async def _keep_typing() -> None:
        while True:
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    typing_task = asyncio.create_task(_keep_typing())
    framed = (
        "[РЕЖИМ: PROGRAMMING / изрично извикано през /practice. Дай пълно, вярно решение "
        "по правилата от Programming Mode секцията: идея → код → тестове (изпълни ги с "
        "code_run, докато всички минат) → сложност.]\n\n"
        f"Задача:\n{problem}"
    )
    try:
        agent = _get_agent()
        response = await agent.run(framed, user_id=user.id, channel="telegram")
    except Exception as exc:
        logger.exception("/practice failed")
        response = f"Грешка: {exc}"
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    chunks = [response[i:i + 3800] for i in range(0, len(response), 3800)] or ["(празен отговор)"]
    await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=chunks[0])
    for extra in chunks[1:]:
        await context.bot.send_message(chat_id=chat_id, text=extra)


async def cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Изрично запомни нещо: /remember текст за запомняне."""
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп.")
        return
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text("Употреба: /remember <какво да запомня>")
        return
    from bogi.modules import long_term_memory
    try:
        mem_id = await long_term_memory.save_memory(
            user_id=user.id, content=text, kind="fact", pinned=False
        )
        await update.message.reply_text(f"📌 Запомнено (id={mem_id}).")
    except Exception as exc:
        logger.exception("Remember failed")
        await update.message.reply_text(f"Грешка: {exc}")


async def cmd_memories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Списък със запазените memories."""
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп.")
        return
    from bogi.modules import long_term_memory
    mems = await long_term_memory.list_memories(user_id=user.id, limit=50)
    if not mems:
        await update.message.reply_text("Нямам memories за теб още. Ползвай /remember за добавяне.")
        return
    lines = [f"📋 *Memories* ({len(mems)}):\n"]
    for m in mems:
        tag = "📌" if m["pinned"] else "·"
        ns = m.get("namespace") or "general"
        lines.append(f"{tag} `#{m['id']}` `{ns}` [{m['kind']}] {m['content'][:200]}")
    text = "\n".join(lines)
    chunks = [text[i:i+3800] for i in range(0, len(text), 3800)]
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Забрави memory: /forget <id> или /forget <описание>."""
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп.")
        return
    arg = " ".join(context.args).strip() if context.args else ""
    if not arg:
        await update.message.reply_text("Употреба: /forget <id> или /forget <описание>")
        return
    from bogi.modules import long_term_memory
    if arg.isdigit():
        ok = await long_term_memory.forget_memory(int(arg), user.id)
        await update.message.reply_text("Забравено." if ok else "Няма такъв memory (или не е твой).")
    else:
        n = await long_term_memory.forget_by_query(user.id, arg)
        await update.message.reply_text("Забравено." if n else "Не намерих достатъчно близък memory.")


async def cmd_new_thread(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Архивира текущия разговор и започва нов (изтрива paметта на агента за теб)."""
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп.")
        return
    from bogi.modules import memory
    new_id = await memory.new_thread(user.id)
    await update.message.reply_text(f"Нов разговор стартиран (thread #{new_id}). Предишният е архивиран.")


async def cmd_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Стартира мониторинг на Moodle личния inbox."""
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп.")
        return

    await update.message.reply_text("Инициализирам мониторинг...")

    # Fetch current message_id so we don't trigger on existing messages
    try:
        agent = _get_agent()
        current = await agent.fmi.get_latest_inbox_message()
        last_id = current.get("message_id", "") if current else ""
    except Exception:
        last_id = ""
        logger.exception("Failed to fetch initial message state")

    _monitor_state[user.id] = {"last_message_id": last_id, "last_content": None, "running": False}

    current_jobs = context.job_queue.get_jobs_by_name(f"monitor_{user.id}")
    for job in current_jobs:
        job.schedule_removal()

    context.job_queue.run_repeating(
        _check_moodle_monitor,
        interval=10,
        first=10,
        name=f"monitor_{user.id}",
        data={"user_id": user.id, "chat_id": update.effective_chat.id},
    )
    await update.message.reply_text(f"Мониторинг стартиран. Последен msg_id: {last_id or 'няма'}. Проверявам на всеки 10с.")


async def cmd_monitor_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Спира мониторинга на Moodle форум."""
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп.")
        return

    jobs = context.job_queue.get_jobs_by_name(f"monitor_{user.id}")
    for job in jobs:
        job.schedule_removal()
    _monitor_state.pop(user.id, None)
    await update.message.reply_text("Мониторингът е спрян.")


async def _check_moodle_monitor(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Проверява Moodle inbox за ново съобщение и отговаря в Telegram."""
    data = context.job.data
    user_id = data["user_id"]
    chat_id = data["chat_id"]

    state = _monitor_state.get(user_id)
    if not state:
        context.job.schedule_removal()
        return

    # Skip if previous check still running
    if state.get("running"):
        logger.debug("Monitor check skipped — previous still running")
        return
    state["running"] = True

    try:
        agent = _get_agent()
        logger.info("Monitor check: fetching inbox for user %s", user_id)
        msg = await agent.fmi.get_latest_inbox_message()
        logger.info("Monitor check result: %s", msg)
        if not msg or not msg.get("content"):
            return

        msg_id = msg.get("message_id", "")
        content = msg["content"].strip()
        if msg_id and msg_id == state.get("last_message_id"):
            return  # няма ново
        if not msg_id and content == state.get("last_content"):
            return

        state["last_message_id"] = msg_id
        state["last_content"] = content

        response = await agent.run(
            f"Написах си ново съобщение в Moodle:\n\n{content}\n\n"
            "Помогни ми с това — обясни, отговори или разработи идеята.",
            user_id=user_id,
            channel="moodle_self",
        )

        # Send response back into Moodle self-chat (not Telegram)
        # Moodle message body has a length limit — chunk if needed
        chunks = [response[i : i + 3500] for i in range(0, len(response), 3500)] or ["(празен отговор)"]
        last_own_id: str | None = None
        for chunk in chunks:
            own_id = await agent.fmi.send_self_message(chunk)
            if own_id:
                last_own_id = own_id

        # Store our own reply id so the next check doesn't treat it as new
        if last_own_id:
            state["last_message_id"] = last_own_id
        state["last_content"] = chunks[-1]
        logger.info("Monitor: replied in Moodle, new last_message_id=%s", last_own_id)
    except Exception:
        logger.exception("Monitor check failed for user %s", user_id)
        try:
            await context.bot.send_message(chat_id=chat_id, text="Грешка в monitor — провери bot.err.log")
        except Exception:
            pass
    finally:
        state["running"] = False


async def send_daily_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Изпраща сутрешния бриф (днешен дневен ред + предстоящи Moodle deadlines) в 8:00.

    Детерминистичен: вика light пътищата директно (`brief.compose_brief`) вместо
    `agent.run`, за да избегне тежкото tool-selection (D-019 — агентът палеше
    `fmi_sync_all_courses_info`). По-бързо, по-евтино, без LLM.
    """
    from bogi.modules import brief

    for user_id in settings.allowed_user_ids:
        try:
            agent = _get_agent()
            with obs.span("job.daily_reminder", source="background", user_id=user_id):
                response = await brief.compose_brief(user_id, agent.fmi)
            chunks = [response[i : i + 3800] for i in range(0, len(response), 3800)] or ["(няма предстоящи задачи)"]
            for chunk in chunks:
                await context.bot.send_message(chat_id=user_id, text=chunk)
        except Exception:
            logger.exception("Daily reminder failed for user %s", user_id)


async def check_web_monitors(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hourly: check active web/price monitors (Life-OS), ping the user on change."""
    from bogi.modules import monitors

    for user_id in settings.allowed_user_ids:
        try:
            with obs.span("job.web_monitors", source="background", user_id=user_id):
                changes = await monitors.check_all(user_id)
            for ch in changes:
                if not ch.get("changed"):
                    continue
                name = ch.get("name") or "монитор"
                new = str(ch.get("new") or "")[:300]
                await context.bot.send_message(
                    chat_id=user_id, text=f"🔔 Промяна: {name}\n{new}"
                )
        except Exception:
            logger.exception("Web monitor check failed for user %s", user_id)


# ---- Calendar reminder watcher (every 5 min) -------------------------------
# In-memory cache of event IDs already reminded. Lives for process lifetime.
# On restart we may re-remind one event in worst case — acceptable.
_reminded_event_ids: set[str] = set()


async def check_calendar_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Every 5 min: notify on events starting in ~25-35 min that weren't yet reminded."""
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from bogi.modules import gcal

    try:
        with obs.span("job.calendar_reminders", source="background"):
            events = await gcal.upcoming(days=1)
    except Exception:
        logger.exception("Calendar reminder check failed")
        return

    now = _dt.now(UTC)
    lower = now + _td(minutes=25)
    upper = now + _td(minutes=35)

    for ev in events:
        ev_id = ev.get("id") or ""
        if not ev_id or ev_id in _reminded_event_ids:
            continue
        start_raw = ev.get("start") or ""
        if not start_raw or ev.get("all_day"):
            continue
        try:
            start_dt = _dt.fromisoformat(start_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=UTC)
        if not (lower <= start_dt <= upper):
            continue

        for user_id in settings.allowed_user_ids:
            try:
                local_time = start_dt.astimezone()
                location = f"\n📍 {ev['location']}" if ev.get("location") else ""
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"⏰ След ~30 мин:\n"
                        f"📅 {ev.get('summary', '(no title)')}\n"
                        f"🕒 {local_time.strftime('%H:%M')}{location}"
                    ),
                )
            except Exception:
                logger.exception("Failed to send calendar reminder to %s", user_id)
        _reminded_event_ids.add(ev_id)
        logger.info("Reminded event %s (%s)", ev_id, ev.get("summary", ""))


# ---- Moodle deadline watcher (every 6h) ------------------------------------
# Cache of (title, time_text, course) tuples seen in last poll. On startup
# we seed the cache silently — first-run does NOT notify, otherwise restart
# would flood the user.
_known_moodle_deadlines: set[tuple[str, str, str]] | None = None


def _moodle_key(ev: dict) -> tuple[str, str, str]:
    return (ev.get("title", ""), ev.get("time_text", ""), ev.get("course", ""))


async def check_moodle_deadlines(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Every 6h: fetch Moodle upcoming events, alert on new ones."""
    global _known_moodle_deadlines
    agent = _get_agent()
    try:
        with obs.span("job.moodle_deadlines", source="background"):
            events = await agent.fmi.get_upcoming_events()
    except Exception:
        logger.exception("Moodle deadline poll failed")
        return

    current_keys = {_moodle_key(e) for e in events}

    if _known_moodle_deadlines is None:
        _known_moodle_deadlines = current_keys
        logger.info("Moodle watcher: seeded %d known deadlines (no alerts)", len(current_keys))
        return

    new_keys = current_keys - _known_moodle_deadlines
    if not new_keys:
        return

    new_events = [e for e in events if _moodle_key(e) in new_keys]
    lines = ["🆕 *Нови задания/тестове в Moodle:*\n"]
    for e in new_events:
        kind_emoji = {"assignment": "📝", "quiz": "📋"}.get(e.get("kind", ""), "📌")
        title = e.get("title", "(без заглавие)")
        course = e.get("course", "")
        when = e.get("time_text", "")
        lines.append(f"{kind_emoji} *{title}*")
        if course:
            lines.append(f"   📚 {course}")
        if when:
            lines.append(f"   ⏰ {when}")
        lines.append("")

    text = "\n".join(lines)
    for user_id in settings.allowed_user_ids:
        try:
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
        except Exception:
            logger.exception("Failed to send Moodle alert to %s", user_id)

    _known_moodle_deadlines = current_keys
    logger.info("Moodle watcher: %d new deadlines alerted", len(new_keys))


async def _register_commands_menu(app: Application) -> None:
    """Tell Telegram which commands to show in the `/` autocomplete menu."""
    from telegram import BotCommand
    commands = [
        BotCommand("help", "Списък на всички команди"),
        BotCommand("upcoming", "Предстоящи домашни и тестове от Moodle"),
        BotCommand("practice", "Programming practice — пълно решение + тестове"),
        BotCommand("remember", "Запомни нещо дългосрочно"),
        BotCommand("memories", "Покажи всичко запомнено"),
        BotCommand("forget", "Забрави memory по id или описание"),
        BotCommand("new", "Нов разговор (забравям контекста)"),
        BotCommand("monitor", "Монитор на Moodle self-chat"),
        BotCommand("monitor_stop", "Спри Moodle монитор"),
        BotCommand("id", "Покажи моя Telegram ID"),
        BotCommand("start", "Поздрав"),
    ]
    try:
        await app.bot.set_my_commands(commands)
        logger.info("Telegram command menu registered (%d entries)", len(commands))
    except Exception:
        logger.exception("Failed to register Telegram command menu")


def _approval_keyboard(approval_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Одобри", callback_data=f"appr:{approval_id}:yes"),
            InlineKeyboardButton("❌ Откажи", callback_data=f"appr:{approval_id}:no"),
        ]]
    )


async def cmd_approvals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показва чакащите одобрение действия с inline бутони."""
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await update.message.reply_text("Нямаш достъп до този бот.")
        return
    from bogi.modules import approvals
    pending = await approvals.list_pending(user.id)
    if not pending:
        await update.message.reply_text("Няма чакащи одобрения. ✅")
        return
    for a in pending:
        await update.message.reply_text(
            f"⏳ Заявка #{a['id']}\n{a['preview']}",
            reply_markup=_approval_keyboard(a["id"]),
        )


async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resolve-ва approval при натиснат inline бутон. Само allowlisted user."""
    query = update.callback_query
    if query is None:
        return
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        await query.answer("Нямаш достъп.", show_alert=True)
        return
    try:
        _, raw_id, decision_token = query.data.split(":", 2)
        approval_id = int(raw_id)
    except (ValueError, AttributeError):
        await query.answer("Невалидни данни.", show_alert=True)
        return

    from bogi.modules import approvals
    decision = approvals.APPROVED if decision_token == "yes" else approvals.REJECTED
    with obs.span(
        "approval.decision", source="telegram", approval_id=approval_id, decision=decision
    ):
        row = await approvals.resolve(approval_id, decision, decided_by=user.id)
    if row is None:
        await query.answer("Заявката не е намерена.", show_alert=True)
        return

    status = row["status"]
    mark = {"approved": "✅ Одобрено", "rejected": "❌ Отказано", "expired": "⌛ Изтекло"}.get(
        status, status
    )
    result_note = ""
    if row["status"] == approvals.APPROVED and row.get("_just_decided"):
        from bogi.modules import approval_exec
        if approval_exec.has_executor(row["tool_name"]):
            try:
                result_note = "\n" + await approval_exec.run(row["tool_name"], row["payload"])
            except Exception as exc:
                logger.exception("Approval executor failed")
                result_note = f"\n⚠️ Одобрено, но изпълнението падна: {exc}"
    await query.answer(mark)
    try:
        await query.edit_message_text(f"{mark}\n{row['preview']}{result_note}")
    except Exception:
        logger.debug("Could not edit approval message (already edited?)", exc_info=True)


def build_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не е зададен в .env")

    app = Application.builder().token(settings.telegram_bot_token).post_init(_register_commands_menu).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("upcoming", cmd_upcoming))
    app.add_handler(CommandHandler("practice", cmd_practice))
    app.add_handler(CommandHandler("new", cmd_new_thread))
    app.add_handler(CommandHandler("remember", cmd_remember))
    app.add_handler(CommandHandler("memories", cmd_memories))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("monitor", cmd_monitor))
    app.add_handler(CommandHandler("monitor_stop", cmd_monitor_stop))
    app.add_handler(CommandHandler("approvals", cmd_approvals))
    app.add_handler(CallbackQueryHandler(handle_approval_callback, pattern=r"^appr:"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if app.job_queue:
        from bogi.tz import local_tz

        app.job_queue.run_daily(send_daily_reminder, time=time(hour=8, minute=0, tzinfo=local_tz()))
        logger.info("Daily reminder scheduled at 08:00 (Europe/Sofia)")
        app.job_queue.run_repeating(
            check_calendar_reminders, interval=5 * 60, first=60, name="calendar_reminders"
        )
        logger.info("Calendar reminders scheduled every 5 min")
        app.job_queue.run_repeating(
            check_moodle_deadlines, interval=6 * 3600, first=120, name="moodle_deadlines"
        )
        logger.info("Moodle deadline watcher scheduled every 6h")
        app.job_queue.run_repeating(
            check_web_monitors, interval=3600, first=180, name="web_monitors"
        )
        logger.info("Web monitor watcher scheduled every 1h")

    return app


async def _on_telegram_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Centralized error handler — keep traceback noise low for known issues."""
    from telegram.error import Conflict, NetworkError

    err = context.error
    if isinstance(err, Conflict):
        # Another polling instance grabbed updates. Singleton lock should make
        # this impossible locally; if it happens, an external instance is live.
        logger.warning("Another Telegram polling instance is active — stopping")
        try:
            await context.application.stop()
        except Exception:
            pass
        return
    if isinstance(err, NetworkError):
        logger.warning("Telegram network error: %s", err)
        return
    logger.exception("Unhandled Telegram error", exc_info=err)


def run() -> None:
    """Polling-based start. За MVP е достатъчно — webhook е v2 upgrade."""
    from bogi.cli import _setup_logging
    from bogi.singleton import acquire_or_exit, release

    _setup_logging(log_file="bot.log")
    obs.configure_logfire()  # no-op unless LOGFIRE_TOKEN is set
    # Singleton: refuse to start if another bot is polling. Avoids the
    # Telegram 409 Conflict death-spiral between competing pollers.
    acquire_or_exit("bogi_bot")
    try:
        app = build_application()
        app.post_shutdown = _on_shutdown
        app.add_error_handler(_on_telegram_error)
        logger.info("BogiAgent Telegram bot стартира (polling)...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        release("bogi_bot")


async def _on_shutdown(app: Application) -> None:
    """Clean async engine on bot shutdown to avoid `Future exception was never retrieved`."""
    try:
        from bogi.db import engine

        await engine.dispose()
        logger.info("DB engine disposed cleanly")
    except Exception:
        logger.exception("Engine dispose failed during shutdown")

    if _agent is not None:
        try:
            await _agent.close()
        except Exception:
            logger.exception("Agent close failed during shutdown")
