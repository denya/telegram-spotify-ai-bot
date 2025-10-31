"""Application entry points for the Telegram Spotify AI Bot."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI

from .bot import commands, playback, playlists
from .config import Settings, load_settings
from .db import schema
from .logging import setup_logging
from .spotify.client import RepositoryTokenStore, SpotifyClient
from .web import create_web_app


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Expose a FastAPI application for ASGI servers."""

    return create_web_app()


app = create_app()


def _configure_bot(
    settings: Settings,
) -> tuple[Bot, Dispatcher, SpotifyClient, RepositoryTokenStore]:
    token_store = RepositoryTokenStore(settings.db_path)
    spotify_client = SpotifyClient(settings=settings, token_store=token_store)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    bot.settings = settings  # type: ignore[attr-defined]
    bot.token_store = token_store  # type: ignore[attr-defined]
    bot.spotify_client = spotify_client  # type: ignore[attr-defined]

    dp = Dispatcher()
    dp.include_router(commands.router)
    dp.include_router(playback.router)
    dp.include_router(playlists.router)
    return bot, dp, spotify_client, token_store


async def run_bot() -> None:
    """Start the aiogram polling loop."""

    setup_logging()
    logger.info("Initializing Telegram bot polling")
    settings = load_settings()
    logger.info(
        "Loaded settings (mode=%s, web_base_url=%s)",
        settings.telegram_mode,
        settings.web_base_url,
    )
    await schema.ensure_schema(settings.db_path)
    bot, dispatcher, spotify_client, _ = _configure_bot(settings)

    try:
        logger.info("Bot is listening for updates…")
        await dispatcher.start_polling(bot)
    finally:
        logger.info("Shutting down Telegram bot")
        await spotify_client.aclose()
        await bot.session.close()


def main() -> None:  # pragma: no cover - convenience wrapper
    """Launch the Telegram bot in polling mode."""

    asyncio.run(run_bot())


def run() -> None:  # pragma: no cover - convenience wrapper
    """Run the FastAPI application using uvicorn."""

    import uvicorn

    setup_logging()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False, factory=False)


if __name__ == "__main__":  # pragma: no cover
    main()
