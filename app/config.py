def load_settings() -> None:
"""Environment-based configuration loader."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from os import getenv
from pathlib import Path
from typing import Final, cast

from dotenv import load_dotenv

from .types import TelegramMode

DEFAULT_DB_PATH: Final[str] = "./data/app.db"
DEFAULT_APP_BASE_URL: Final[str] = "http://localhost:8000"
DEFAULT_TELEGRAM_MODE: Final[TelegramMode] = "polling"
DEFAULT_SPOTIFY_PKCE_ENABLED: Final[bool] = True
DEFAULT_ANTHROPIC_WEB_SEARCH_ENABLED: Final[bool] = True
DEFAULT_ANTHROPIC_WEB_SEARCH_MAX_USES: Final[int] = 3


load_dotenv()


class ConfigurationError(RuntimeError):
    """Raised when required configuration values are missing or invalid."""


def _require(name: str) -> str:
    value = getenv(name)
    if value is None or not value.strip():
        raise ConfigurationError(f"Environment variable '{name}' must be set.")
    return value.strip()


def _bool(name: str, default: bool) -> bool:
    raw = getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y"}:
        return True
    if normalized in {"0", "false", "f", "no", "n"}:
        return False
    raise ConfigurationError(
        f"Environment variable '{name}' must be a boolean-like value (true/false)."
    )


def _int(name: str, default: int) -> int:
    raw = getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - defensive branch
        raise ConfigurationError(f"Environment variable '{name}' must be an integer.") from exc


def _telegram_mode() -> TelegramMode:
    raw = getenv("TELEGRAM_MODE", DEFAULT_TELEGRAM_MODE)
    mode = raw.strip().lower()
    if mode not in {"polling", "webhook"}:
        raise ConfigurationError("TELEGRAM_MODE must be either 'polling' or 'webhook'.")
    return cast(TelegramMode, mode)


@dataclass(frozen=True, slots=True)
class Settings:
    """Strongly typed configuration values backed by environment variables."""

    telegram_bot_token: str
    telegram_mode: TelegramMode
    app_base_url: str

    spotify_client_id: str
    spotify_client_secret: str | None
    spotify_redirect_uri: str
    spotify_pkce_enabled: bool

    anthropic_api_key: str | None
    anthropic_web_search_enabled: bool
    anthropic_web_search_max_uses: int

    db_path: Path
    encryption_key: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
            telegram_mode=_telegram_mode(),
            app_base_url=getenv("APP_BASE_URL", DEFAULT_APP_BASE_URL).rstrip("/"),
            spotify_client_id=_require("SPOTIFY_CLIENT_ID"),
            spotify_client_secret=(getenv("SPOTIFY_CLIENT_SECRET") or None),
            spotify_redirect_uri=_require("SPOTIFY_REDIRECT_URI"),
            spotify_pkce_enabled=_bool("SPOTIFY_PKCE_ENABLED", DEFAULT_SPOTIFY_PKCE_ENABLED),
            anthropic_api_key=(getenv("ANTHROPIC_API_KEY") or None),
            anthropic_web_search_enabled=_bool(
                "ANTHROPIC_WEB_SEARCH_ENABLED", DEFAULT_ANTHROPIC_WEB_SEARCH_ENABLED
            ),
            anthropic_web_search_max_uses=_int(
                "ANTHROPIC_WEB_SEARCH_MAX_USES", DEFAULT_ANTHROPIC_WEB_SEARCH_MAX_USES
            ),
            db_path=Path(getenv("DB_PATH", DEFAULT_DB_PATH)),
            encryption_key=(getenv("ENCRYPTION_KEY") or None),
        )


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Return cached settings constructed from the environment."""

    return Settings.from_env()


__all__ = ["ConfigurationError", "Settings", "load_settings"]
