from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from os import getenv
from pathlib import Path
from typing import Final, cast

from dotenv import load_dotenv

from .types import TelegramMode

DEFAULT_DB_PATH: Final[str] = "./data/app.db"
DEFAULT_WEB_BASE_URL: Final[str] = "http://localhost:8000"
DEFAULT_TELEGRAM_MODE: Final[TelegramMode] = "polling"
DEFAULT_SPOTIFY_PKCE_ENABLED: Final[bool] = True
DEFAULT_ANTHROPIC_WEB_SEARCH_ENABLED: Final[bool] = True
DEFAULT_ANTHROPIC_WEB_SEARCH_MAX_USES: Final[int] = 3
DEFAULT_ANTHROPIC_MODEL: Final[str] = "claude-sonnet-4-5"

SPOTIFY_SCOPES: Final[tuple[str, ...]] = (
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-top-read",
    "user-read-recently-played",
)


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


def _resolve_base_url(redirect_uri: str) -> str:
    """Extract base URL from SPOTIFY_REDIRECT_URI by removing /spotify/callback."""
    redirect_uri = redirect_uri.strip().rstrip("/")
    if redirect_uri.endswith("/spotify/callback"):
        base_url = redirect_uri[: -len("/spotify/callback")]
        return base_url.rstrip("/")
    # If format doesn't match, return default
    return DEFAULT_WEB_BASE_URL


@dataclass(frozen=True, slots=True)
class Settings:
    """Strongly typed configuration values backed by environment variables."""

    telegram_bot_token: str
    telegram_mode: TelegramMode
    web_base_url: str

    spotify_client_id: str
    spotify_client_secret: str | None
    spotify_redirect_uri: str
    spotify_pkce_enabled: bool

    anthropic_api_key: str | None
    anthropic_model: str
    anthropic_web_search_enabled: bool
    anthropic_web_search_max_uses: int

    db_path: Path
    encryption_key: str | None
    administrator_user_id: int | None

    @classmethod
    def from_env(cls) -> Settings:
        spotify_redirect_uri = _require("SPOTIFY_REDIRECT_URI")
        web_base_url = _resolve_base_url(spotify_redirect_uri)
        administrator_user_id = getenv("ADMINISTRATOR_USER_ID")
        admin_id = int(administrator_user_id) if administrator_user_id else None
        return cls(
            telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
            telegram_mode=_telegram_mode(),
            web_base_url=web_base_url,
            spotify_client_id=_require("SPOTIFY_CLIENT_ID"),
            spotify_client_secret=(getenv("SPOTIFY_CLIENT_SECRET") or None),
            spotify_redirect_uri=spotify_redirect_uri,
            spotify_pkce_enabled=_bool("SPOTIFY_PKCE_ENABLED", DEFAULT_SPOTIFY_PKCE_ENABLED),
            anthropic_api_key=(getenv("ANTHROPIC_API_KEY") or None),
            anthropic_model=(
                getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL).strip()
                or DEFAULT_ANTHROPIC_MODEL
            ),
            anthropic_web_search_enabled=_bool(
                "ANTHROPIC_WEB_SEARCH_ENABLED", DEFAULT_ANTHROPIC_WEB_SEARCH_ENABLED
            ),
            anthropic_web_search_max_uses=_int(
                "ANTHROPIC_WEB_SEARCH_MAX_USES", DEFAULT_ANTHROPIC_WEB_SEARCH_MAX_USES
            ),
            db_path=Path(getenv("DB_PATH", DEFAULT_DB_PATH)),
            encryption_key=(getenv("ENCRYPTION_KEY") or None),
            administrator_user_id=admin_id,
        )


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Return cached settings constructed from the environment."""

    return Settings.from_env()


__all__ = ["SPOTIFY_SCOPES", "ConfigurationError", "Settings", "load_settings"]
