"""Draft generation. Drafts се пишат САМО в vault/inbox/. Никога не се изпраща."""

from __future__ import annotations

from datetime import datetime
from textwrap import dedent

from bogi.modules import obsidian


async def draft_email(
    recipient_role: str,
    topic: str,
    body_outline: str,
    language: str = "bg",
    sender_name: str = "Богдан",
) -> dict:
    """Записва чернова на имейл в `vault/inbox/`.

    Този tool НЕ генерира текста с LLM — той само форматира dadeniya outline
    в имейл template и го записва. LLM-ът (агентът) подава вече готов outline.
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M")

    if language == "bg":
        salutation = f"Уважаеми {recipient_role},"
        signature = dedent(
            f"""
            С уважение,
            {sender_name}
            """
        ).strip()
    else:
        salutation = f"Dear {recipient_role},"
        signature = f"Best regards,\n{sender_name}"

    content = dedent(
        f"""
        ---
        type: draft_email
        recipient_role: "{recipient_role}"
        topic: "{topic}"
        language: {language}
        status: draft
        created: {datetime.now().isoformat()}
        ---

        # Чернова: {topic}

        **До:** {recipient_role}
        **Тема:** {topic}

        ---

        {salutation}

        {body_outline.strip()}

        {signature}
        """
    ).lstrip()

    filename = f"email-{topic[:40]}-{timestamp}.md"
    return obsidian.vault_write_draft(filename, content)


async def draft_message(topic: str, content: str, tone: str = "casual") -> dict:
    """Кратко съобщение (Telegram/Discord стил). Записва в vault/inbox/."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M")

    md = dedent(
        f"""
        ---
        type: draft_message
        topic: "{topic}"
        tone: {tone}
        created: {datetime.now().isoformat()}
        ---

        # {topic}

        {content.strip()}
        """
    ).lstrip()

    filename = f"msg-{topic[:40]}-{timestamp}.md"
    return obsidian.vault_write_draft(filename, md)
