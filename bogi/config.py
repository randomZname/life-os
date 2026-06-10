"""Configuration loaded from .env via Pydantic Settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+asyncpg://bogi:bogi@localhost:5432/bogi"

    # LiteLLM proxy
    litellm_base_url: str = "http://localhost:4000"
    litellm_master_key: str = "sk-bogi-local-change-me"
    default_model: str = "smart"

    # Provider keys (LiteLLM ги чете директно от env, тук са само за reference)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    # FMI Moodle
    fmi_base_url: str = "https://learn.fmi.uni-sofia.bg"
    fmi_username: str = ""
    fmi_password: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""

    # Paths
    data_dir: str = "./data"
    vault_path: str = "./vault"
    # Subfolder inside the vault where the agent writes drafts/notes. Defaults to
    # the real Obsidian vault's Inbox ("00_Inbox"); override via VAULT_INBOX_SUBDIR.
    vault_inbox_subdir: str = "00_Inbox"

    # Google Calendar
    gcal_calendar_id: str = "primary"  # Override to sync a specific calendar
    gcal_timezone: str = "Europe/Sofia"

    # Embeddings
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimension: int = 384

    # Cost guards
    monthly_soft_budget_usd: float = 30.0
    monthly_hard_budget_usd: float = 100.0
    # Max LLM requests (agent steps) per single run — runaway-loop guard. Bounds
    # agent steps, NOT internal tool loops (a tool that loops internally = 1 step).
    # Claude is flat-rate OAuth, so this protects latency + caps the OpenRouter
    # fallback rather than the Claude bill. Raise if a normal flow ever hits it.
    agent_request_limit: int = 30

    # Web dashboard (Phase 5) — local Starlette app behind Cloudflare Tunnel.
    web_host: str = "127.0.0.1"
    web_port: int = 8787
    web_username: str = ""        # set via `bogi web-auth` (lives in .env)
    web_password_hash: str = ""   # pbkdf2 string; set via `bogi web-auth`
    web_session_secret: str = ""  # random; set via `bogi web-auth`
    web_login_max_attempts: int = 5
    web_login_lockout_minutes: int = 15
    # Secure (HTTPS-only) session cookie. True in production (behind Cloudflare =
    # HTTPS to the browser). Set WEB_SECURE_COOKIE=false ONLY for local http smoke.
    web_secure_cookie: bool = True
    # Require login on the dashboard. Default ON (secure by default). Set
    # WEB_AUTH_ENABLED=false ONLY for a loopback-bound local server — disabling it
    # on a non-loopback host exposes everything unauthenticated (web_cmd warns).
    web_auth_enabled: bool = True

    # Logging
    log_level: str = "INFO"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).resolve()

    @property
    def courses_path(self) -> Path:
        return self.data_path / "courses"

    @property
    def files_path(self) -> Path:
        """Папка където агентът пази/сваля файлове по поръчка."""
        return self.data_path / "files"

    @property
    def gcal_dir(self) -> Path:
        return self.data_path / "gcal"

    @property
    def gcal_client_secret(self) -> Path:
        return self.gcal_dir / "client_secret.json"

    @property
    def gcal_token(self) -> Path:
        return self.gcal_dir / "token.json"

    @property
    def vault_root(self) -> Path:
        return Path(self.vault_path).resolve()

    @property
    def vault_inbox(self) -> Path:
        return self.vault_root / self.vault_inbox_subdir

    @property
    def allowed_user_ids(self) -> list[int]:
        if not self.telegram_allowed_user_ids:
            return []
        return [int(x.strip()) for x in self.telegram_allowed_user_ids.split(",") if x.strip()]


settings = Settings()
