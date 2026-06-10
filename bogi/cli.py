"""Typer CLI for BogiAgent.

Usage:
    bogi --help
    bogi fmi-test
    bogi ingest <file>
    bogi search "query"
    bogi chat
    bogi telegram
    bogi list-courses
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from bogi.agent import BogiAgent
from bogi.config import settings
from bogi.modules import documents
from bogi.modules.fmi import FMIScraper
from bogi.redaction import RedactingFilter

app = typer.Typer(help="BogiAgent CLI")


def _setup_console_encoding() -> None:
    """Make Rich/Typer output work in Windows terminals using legacy encodings."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


_setup_console_encoding()
console = Console()


from logging.handlers import RotatingFileHandler


class _SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that never lets logging die on Windows.

    On Windows the rollover `os.rename` fails if any other handle holds the log
    (watchdog tailing it, an open editor, antivirus). The stdlib handler then
    raises on every `emit`, which `logging` dumps to stderr — flooding it (we hit
    a 37 MB bot.err.log) while the real log froze at the rotation size. Here, if a
    rollover fails, we truncate the current file in place and keep writing:
    bounded, alive, no stderr flood.
    """

    def doRollover(self) -> None:  # noqa: N802 (stdlib name)
        try:
            super().doRollover()
        except Exception:
            try:
                if self.stream:
                    self.stream.close()
                    self.stream = None
                # Reset the current file to empty (accept log loss over a crash).
                open(self.baseFilename, "w", encoding=self.encoding or "utf-8").close()
            except Exception:
                pass
            self.stream = self._open()


def _setup_logging(log_file: str | None = None) -> None:
    """Configure root logger with console + optional rotating file handler.

    Idempotent: if root already has handlers from a previous call, do nothing.
    """
    root = logging.getLogger()
    if root.handlers:
        return

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root.setLevel(settings.log_level)

    redaction_filter = RedactingFilter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.addFilter(redaction_filter)
    root.addHandler(console_handler)

    if log_file:
        file_handler = _SafeRotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.addFilter(redaction_filter)
        root.addHandler(file_handler)


@app.command()
def fmi_test() -> None:
    """Тест: login във ФМИ Moodle и листване на курсове."""
    _setup_logging()

    async def _run() -> None:
        scraper = FMIScraper()
        try:
            console.print("[yellow]Логване във ФМИ...[/yellow]")
            ok = await scraper.login()
            if not ok:
                console.print("[red]Login пропадна. Провери FMI_USERNAME / FMI_PASSWORD.[/red]")
                return

            console.print("[green]Login успешен. Сваляне на курсове...[/green]")
            courses = await scraper.get_courses()

            table = Table(title="Курсове във ФМИ")
            table.add_column("ID", style="cyan")
            table.add_column("Име")
            table.add_column("URL", style="dim")
            for c in courses:
                table.add_row(c["fmi_id"], c["name"], c["url"])
            console.print(table)
        finally:
            await scraper.close()

    asyncio.run(_run())


@app.command()
def ingest(file: Path, source: str = "manual") -> None:
    """Ingest файл в pgvector."""
    _setup_logging()

    async def _run() -> None:
        result = await documents.document_ingest(str(file), source=source)
        console.print(result)

    asyncio.run(_run())


@app.command()
def search(query: str, k: int = 5) -> None:
    """Semantic search в ingest-натите документи."""
    _setup_logging()

    async def _run() -> None:
        results = await documents.document_search(query, k=k)
        for i, r in enumerate(results, 1):
            console.print(f"\n[bold cyan]#{i}[/bold cyan] {r['title']} (score={r['score']:.3f})")
            console.print(r["text"][:500])

    asyncio.run(_run())


@app.command("list-docs")
def list_docs(limit: int = 50) -> None:
    """Списък на ingest-натите документи."""
    _setup_logging()

    async def _run() -> None:
        docs = await documents.document_list(limit=limit)
        table = Table(title="Documents")
        table.add_column("ID", style="cyan")
        table.add_column("Title")
        table.add_column("Source")
        table.add_column("Course")
        for d in docs:
            table.add_row(str(d["id"]), d["title"] or "-", d["source"], d["course"] or "-")
        console.print(table)

    asyncio.run(_run())


@app.command()
def chat() -> None:
    """Интерактивен chat с BogiAgent в терминала."""
    _setup_logging()
    from bogi import observability as obs

    obs.configure_logfire()  # no-op unless LOGFIRE_TOKEN is set
    console.print("[bold cyan]BogiAgent CLI chat[/bold cyan] (Ctrl+C за изход)\n")

    async def _run() -> None:
        agent = BogiAgent()
        try:
            while True:
                try:
                    prompt = console.input("[bold green]ти > [/bold green]").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not prompt:
                    continue
                if prompt.lower() in {"exit", "quit", "q"}:
                    break
                try:
                    response = await agent.run(prompt, user_id=0, channel="cli")
                    console.print(f"[bold magenta]боги > [/bold magenta]{response}\n")
                except Exception as exc:
                    console.print(f"[red]Грешка: {exc}[/red]\n")
        finally:
            await agent.close()

    asyncio.run(_run())


@app.command()
def telegram() -> None:
    """Стартира Telegram бота (polling)."""
    from bogi.telegram_bot import run

    run()


@app.command()
def init_db() -> None:
    """Създава vector extension и tables (за бърз dev). За production ползвай Alembic."""
    _setup_logging()

    async def _run() -> None:
        from sqlalchemy import text

        from bogi.db import engine
        from bogi.models import Base

        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS chunks_embedding_idx "
                    "ON chunks USING hnsw (embedding vector_cosine_ops)"
                )
            )
        console.print("[green]✓ DB инициализирана.[/green]")

    asyncio.run(_run())


@app.command("gcal-auth")
def gcal_auth() -> None:
    """Първоначална Google Calendar OAuth — отваря браузър за оторизация."""
    _setup_logging()
    from bogi.modules import gcal

    try:
        path = gcal.authorize_interactive()
        console.print(f"[green]✓ Token saved to {path}[/green]")
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)


@app.command("gmail-auth")
def gmail_auth() -> None:
    """Първоначална Gmail OAuth (read-only) — отделен токен, не пипа календара."""
    _setup_logging()
    from bogi.modules import gmail

    try:
        path = gmail.authorize_interactive()
        console.print(f"[green]✓ Gmail token saved to {path}[/green]")
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)


@app.command("gcal-test")
def gcal_test(days: int = 7) -> None:
    """Тест: листва предстоящи събития от Google Calendar."""
    _setup_logging()

    async def _run() -> None:
        from bogi.modules import gcal

        try:
            events = await gcal.upcoming(days=days)
        except Exception as exc:
            console.print(f"[red]Грешка: {exc}[/red]")
            return

        if not events:
            console.print("[yellow](няма събития в избрания период)[/yellow]")
            return

        table = Table(title=f"Google Calendar — следващите {days} дни")
        table.add_column("Start", style="cyan")
        table.add_column("Title")
        table.add_column("Location", style="dim")
        for e in events:
            table.add_row(e["start"], e["summary"], e["location"])
        console.print(table)

    asyncio.run(_run())


@app.command()
def status() -> None:
    """Покажи статус на singleton lock-овете и съответните процеси."""
    from bogi.singleton import status as lock_status

    table = Table(title="BogiAgent runtime status")
    table.add_column("Lock", style="cyan")
    table.add_column("State")
    table.add_column("PID", style="dim")
    table.add_column("Started")
    table.add_column("Command", overflow="fold")

    for name in ("bogi_watchdog", "bogi_bot"):
        state, info = lock_status(name)
        if info is None:
            table.add_row(name, state, "-", "-", "-")
        else:
            table.add_row(name, state, str(info.pid), info.started_at, info.cmdline)

    console.print(table)
    console.print(
        "\n[dim]Production start:[/dim]  [cyan].venv/Scripts/python watchdog.py[/cyan]"
    )
    console.print(
        "[dim]Debug (no watchdog):[/dim]  [cyan].venv/Scripts/python -m bogi telegram[/cyan]"
    )
    console.print(
        "[dim]НЕ стартирай и двете едновременно — singleton ще блокира второто.[/dim]\n"
    )


@app.command("eval")
def eval_cmd() -> None:
    """Пуска behavioral eval сценариите срещу агента (live LLM). Връща pass/fail таблица."""
    _setup_logging()
    from evals.runner import format_report, run_all

    async def _run() -> None:
        results = await run_all()
        console.print(format_report(results))

    asyncio.run(_run())


@app.command("web")
def web_cmd() -> None:
    """Стартира локалния web dashboard (Starlette + uvicorn) на 127.0.0.1."""
    _setup_logging()
    import uvicorn

    if not settings.web_auth_enabled:
        _loopback = settings.web_host in ("127.0.0.1", "localhost", "::1")
        if _loopback:
            console.print("[yellow]⚠ Логинът е ИЗКЛЮЧЕН (WEB_AUTH_ENABLED=false) — само за локален достъп.[/]")
        else:
            console.print(
                f"[bold red]⛔ ЛОГИНЪТ Е ИЗКЛЮЧЕН на НЕ-loopback хост ({settings.web_host}) — "
                "dashboard-ът е ОТВОРЕН без парола! Сложи WEB_AUTH_ENABLED=true или host=127.0.0.1.[/]"
            )
    elif not settings.web_username or not settings.web_password_hash:
        console.print("[yellow]⚠ Няма логин конфигуриран — пусни `bogi web-auth` първо.[/]")
    console.print(
        f"[cyan]Dashboard:[/] http://{settings.web_host}:{settings.web_port}  "
        "(локално; за телефон → Cloudflare Tunnel)"
    )
    uvicorn.run(
        "bogi.web.app:build_app",
        host=settings.web_host,
        port=settings.web_port,
        factory=True,
        log_level=settings.log_level.lower(),
    )


def _write_env(updates: dict[str, str]) -> None:
    """Update/append KEY=VALUE lines in .env, preserving the rest."""
    env_path = Path(".env")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for key, val in remaining.items():
        out.append(f"{key}={val}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


@app.command("web-auth")
def web_auth_cmd() -> None:
    """Сетва username + парола за web dashboard-а (записва хеш + secret в .env)."""
    import getpass
    import secrets

    from bogi.web.auth import hash_password

    username = input("Username: ").strip()
    if not username:
        console.print("[red]Празен username.[/]")
        raise typer.Exit(1)
    pw1 = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Repeat password: ")
    if not pw1 or pw1 != pw2:
        console.print("[red]Паролите не съвпадат (или са празни).[/]")
        raise typer.Exit(1)
    _write_env(
        {
            "WEB_USERNAME": username,
            "WEB_PASSWORD_HASH": hash_password(pw1),
            "WEB_SESSION_SECRET": secrets.token_urlsafe(48),
        }
    )
    console.print("[green]✓ Записано в .env (WEB_USERNAME / WEB_PASSWORD_HASH / WEB_SESSION_SECRET).[/]")
    console.print("Рестартирай `bogi web`, за да влезе в сила.")


@app.command()
def version() -> None:
    """Версия."""
    from bogi import __version__

    console.print(f"BogiAgent {__version__}")


if __name__ == "__main__":
    app()
