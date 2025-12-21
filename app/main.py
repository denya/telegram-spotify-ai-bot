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
from aiogram.types import BotCommand
from fastapi import FastAPI

from .bot import commands, playback, playlists, search
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
    dp.include_router(search.router)
    return bot, dp, spotify_client, token_store


async def _set_bot_commands(bot: Bot) -> None:
    """Set bot commands for command suggestions in Telegram."""
    bot_commands = [
        BotCommand(command="start", description="Start the bot and connect Spotify"),
        BotCommand(command="help", description="Show available commands and help"),
        BotCommand(command="now", description="Show current track and playback controls"),
        BotCommand(command="mix", description="Generate an AI-powered playlist"),
        BotCommand(command="search", description="Find songs from vibes, lyrics, or memories"),
    ]
    try:
        await bot.set_my_commands(bot_commands)
        logger.info("Bot commands registered successfully")
    except Exception as exc:
        logger.warning("Failed to set bot commands: %s", exc, exc_info=True)


async def _start_bot_polling(
    dispatcher: Dispatcher,
    bot: Bot,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    async def _stop_signal() -> bool:
        if stop_event is None:
            return False
        # Check if event is set without waiting
        if stop_event.is_set():
            return True
        # Use a very short timeout to check periodically
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=0.1)
            return True
        except TimeoutError:
            return False

    await dispatcher.start_polling(bot, handle_signals=False, stop_signal=_stop_signal)


async def run_bot() -> None:
    """Start the aiogram polling loop."""

    setup_logging()
    logger.info("Initializing Telegram bot polling")
    settings = load_settings()
    logger.info("Configuration loaded:")
    logger.info("  Telegram mode: %s", settings.telegram_mode)
    logger.info("  Web base URL: %s", settings.web_base_url)
    logger.info("  Spotify redirect URI: %s", settings.spotify_redirect_uri)
    logger.info("  Login URL: %s/spotify/login", settings.web_base_url)
    await schema.ensure_schema(settings.db_path)
    bot, dispatcher, spotify_client, _ = _configure_bot(settings)

    try:
        await _set_bot_commands(bot)
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
    logger.info("Configuration loaded:")
    logger.info("  Telegram mode: %s", settings.telegram_mode)
    logger.info("  Web base URL: %s", settings.web_base_url)
    logger.info("  Spotify redirect URI: %s", settings.spotify_redirect_uri)
    logger.info("  Login URL: %s/spotify/login", settings.web_base_url)
    await schema.ensure_schema(settings.db_path)
    bot, dispatcher, spotify_client, _ = _configure_bot(settings)

    await _set_bot_commands(bot)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    shutdown_initiated = False

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

    def _trigger_stop() -> None:
        nonlocal shutdown_initiated
        if shutdown_initiated:
            return  # Prevent multiple calls
        shutdown_initiated = True
        logger.info("Shutdown signal received; stopping services")
        stop_event.set()
        server.should_exit = True
        # Cancel tasks asynchronously
        for task in tasks:
            if not task.done():
                task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _trigger_stop)

    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                logger.debug("Task %s was cancelled", task.get_name())
            except Exception as e:
                logger.error("Task %s raised exception: %s", task.get_name(), e, exc_info=True)
    except Exception as e:
        logger.error("Error in combined mode: %s", e, exc_info=True)
    finally:
        logger.info("Shutting down services...")
        stop_event.set()
        server.should_exit = True

        # Temporarily suppress uvicorn/starlette error logging during shutdown
        uvicorn_logger = logging.getLogger("uvicorn.error")
        starlette_logger = logging.getLogger("starlette")
        original_level = uvicorn_logger.level
        starlette_original_level = starlette_logger.level

        try:
            uvicorn_logger.setLevel(logging.CRITICAL)
            starlette_logger.setLevel(logging.CRITICAL)

            # Give services a moment to shut down gracefully
            await asyncio.sleep(0.1)

            # Wait for tasks to complete naturally first (with a short timeout)
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1.0)
            except TimeoutError:
                # If tasks don't complete naturally, cancel them
                for task in tasks:
                    if not task.done():
                        task.cancel()

                # Wait for cancelled tasks with timeout
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True), timeout=4.0
                    )
                except TimeoutError:
                    logger.warning("Tasks did not complete within timeout, forcing shutdown")
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            # Restore original log levels
            uvicorn_logger.setLevel(original_level)
            starlette_logger.setLevel(starlette_original_level)

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
