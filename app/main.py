"""Application entry points for the Telegram Spotify AI Bot."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from collections.abc import Sequence
from contextlib import suppress

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

DEFAULT_COMBINED_HOST = os.getenv("WEB_HOST", "0.0.0.0")  # noqa: S104
DEFAULT_COMBINED_PORT = int(os.getenv("PORT", "8000"))


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


async def _start_bot_polling(
    dispatcher: Dispatcher,
    bot: Bot,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    async def _stop_signal() -> bool:
        if stop_event is None:
            return False
        await stop_event.wait()
        return True

    await dispatcher.start_polling(bot, handle_signals=False, stop_signal=_stop_signal)


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
        await _start_bot_polling(dispatcher, bot)
    finally:
        logger.info("Shutting down Telegram bot")
        await spotify_client.aclose()
        await bot.session.close()


async def run_combined(
    host: str = DEFAULT_COMBINED_HOST, port: int = DEFAULT_COMBINED_PORT
) -> None:
    """Run FastAPI web app and Telegram bot concurrently."""

    import uvicorn

    setup_logging()
    logger.info("Starting combined mode (web + bot) on %s:%s", host, port)
    settings = load_settings()
    logger.info(
        "Loaded settings (mode=%s, web_base_url=%s)",
        settings.telegram_mode,
        settings.web_base_url,
    )
    await schema.ensure_schema(settings.db_path)
    bot, dispatcher, spotify_client, _ = _configure_bot(settings)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _trigger_stop() -> None:
        logger.info("Shutdown signal received; stopping services")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _trigger_stop)

    config = uvicorn.Config(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
        factory=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    async def _run_web() -> None:
        await server.serve()

    bot_task = asyncio.create_task(
        _start_bot_polling(dispatcher, bot, stop_event=stop_event),
        name="telegram-bot",
    )
    web_task = asyncio.create_task(_run_web(), name="uvicorn-server")
    tasks = {bot_task, web_task}

    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        stop_event.set()
        server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.remove_signal_handler(sig)
        logger.info("Shutting down combined services")
        await spotify_client.aclose()
        await bot.session.close()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram Spotify AI Bot runner")
    parser.add_argument(
        "--combined",
        action="store_true",
        help="Run FastAPI and Telegram bot within a single process",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_COMBINED_HOST,
        help="Host interface for the FastAPI server in combined mode",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_COMBINED_PORT,
        help="Port for the FastAPI server in combined mode",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:  # pragma: no cover - convenience wrapper
    """Process command-line arguments and launch the application."""

    args = _parse_args(argv)
    run_mode = os.getenv("RUN_MODE", "").strip().lower()
    if args.combined or run_mode == "combined":
        asyncio.run(run_combined(host=args.host, port=args.port))
        return

    asyncio.run(run_bot())


def run() -> None:  # pragma: no cover - convenience wrapper
    """Run the FastAPI application using uvicorn."""

    import uvicorn

    setup_logging()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False, factory=False)


if __name__ == "__main__":  # pragma: no cover
    main()
