"""Command handlers for the Telegram Spotify bot."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlencode

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from ..config import Settings
from ..db import repository, schema
from ..spotify.client import RepositoryTokenStore, SpotifyClient, SpotifyClientError
from .keyboards import build_playback_keyboard

router = Router(name="commands")


def _bot_attr(message: Message, name: str) -> Any:
    value = getattr(message.bot, name, None)
    if value is None:
        raise RuntimeError(f"Bot attribute '{name}' is not configured")
    return value


def _get_settings(message: Message) -> Settings:
    return cast(Settings, _bot_attr(message, "settings"))


def _get_token_store(message: Message) -> RepositoryTokenStore:
    return cast(RepositoryTokenStore, _bot_attr(message, "token_store"))


def _get_spotify_client(message: Message) -> SpotifyClient:
    return cast(SpotifyClient, _bot_attr(message, "spotify_client"))


def _build_login_url(settings: Settings, message: Message) -> str:
    if message.from_user is None:
        raise RuntimeError("Unable to determine Telegram user")
    params = {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username or "",
        "first_name": message.from_user.first_name or "",
        "last_name": message.from_user.last_name or "",
    }
    query = urlencode(params)
    return f"{settings.web_base_url}/spotify/login?{query}"


async def _ensure_user_record(settings: Settings, message: Message) -> None:
    if message.from_user is None:
        return
    profile = repository.UserProfile(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    async with repository.connect(settings.db_path) as connection:
        await schema.apply_schema(connection)
        await repository.ensure_user(connection, profile)
        await connection.commit()


async def _load_tokens(message: Message) -> repository.SpotifyTokens | None:
    token_store = _get_token_store(message)
    return (
        await token_store.load_by_telegram_id(message.from_user.id) if message.from_user else None
    )


async def _load_tokens_by_telegram_id(
    message: Message, telegram_id: int
) -> repository.SpotifyTokens | None:
    """Load tokens by Telegram user ID, given a message object."""
    token_store = _get_token_store(message)
    return await token_store.load_by_telegram_id(telegram_id)


def _build_login_url_for_user(
    settings: Settings,
    telegram_id: int,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
) -> str:
    """Build login URL for a specific user without requiring a Message object."""
    params = {
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
    }
    query = urlencode(params)
    return f"{settings.web_base_url}/spotify/login?{query}"


async def _send_link_prompt_for_user(
    message: Message,
    settings: Settings,
    telegram_id: int,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
) -> None:
    """Send login prompt for a specific user without requiring message.from_user."""
    login_url = _build_login_url_for_user(settings, telegram_id, username, first_name, last_name)
    is_localhost = "localhost" in login_url or "127.0.0.1" in login_url

    text = (
        "🎵 <b>Welcome to Spotify AI Bot!</b>\n\n"
        "To get started, let's connect your Spotify account.\n"
        "Click the link below to authorize:\n\n"
        f"<a href='{login_url}'>🔗 Connect Spotify Account</a>"
    )

    if is_localhost:
        text += f"\n\n<b>Localhost URL:</b>\n<code>{login_url}</code>"

    await message.answer(text, parse_mode="HTML")


async def _send_link_prompt(message: Message, settings: Settings) -> None:
    login_url = _build_login_url(settings, message)
    is_localhost = "localhost" in login_url or "127.0.0.1" in login_url

    text = (
        "🎵 <b>Welcome to Spotify AI Bot!</b>\n\n"
        "To get started, let's connect your Spotify account.\n"
        "Click the link below to authorize:\n\n"
        f"<a href='{login_url}'>🔗 Connect Spotify Account</a>"
    )

    if is_localhost:
        text += f"\n\n<b>Localhost URL:</b>\n<code>{login_url}</code>"

    await message.answer(text, parse_mode="HTML")


def _format_track(payload: dict[str, Any]) -> str:
    item = payload.get("item")
    if not isinstance(item, dict):
        return "Nothing is playing right now."
    name = item.get("name") or "Unknown title"
    artists = ", ".join(
        artist.get("name", "?") for artist in item.get("artists", []) if isinstance(artist, dict)
    )
    album = (item.get("album") or {}).get("name") if isinstance(item.get("album"), dict) else None
    external_urls = item.get("external_urls")
    external: dict[str, Any] = external_urls if isinstance(external_urls, dict) else {}
    url = external.get("spotify")
    device = payload.get("device") if isinstance(payload.get("device"), dict) else None
    device_name = device.get("name") if isinstance(device, dict) else None

    bits = [f"🎧 <b>{name}</b>"]
    if artists:
        bits.append(f"by {artists}")
    if album:
        bits.append(f"on {album}")
    if device_name:
        bits.append(f"▶️ {device_name}")
    text = "\n".join(bits)
    if url:
        text += f"\n<a href='{url}'>Open in Spotify</a>"
    return text


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    settings = _get_settings(message)
    await _ensure_user_record(settings, message)
    tokens = await _load_tokens(message)

    if tokens is None:
        await _send_link_prompt(message, settings)
        return

    text = (
        "✅ <b>Spotify is connected!</b>\n\n"
        "You can now control your music. Here are the available commands:\n\n"
        "• <code>/now</code> - See what's currently playing\n"
        "• <code>/mix &lt;vibe&gt;</code> - Generate an AI-powered playlist\n"
        "• <code>/help</code> - Show this help message\n\n"
        "Use the buttons below to control playback:"
    )
    await message.answer(text, reply_markup=build_playback_keyboard(), parse_mode="HTML")


@router.message(Command("now"))
async def handle_now_playing(message: Message) -> None:
    settings = _get_settings(message)
    tokens = await _load_tokens(message)

    if tokens is None:
        await _send_link_prompt(message, settings)
        return

    spotify = _get_spotify_client(message)
    if message.from_user is None:
        await message.answer("I couldn't detect your Telegram account.")
        return

    try:
        playback = await spotify.get_currently_playing(message.from_user.id)
    except SpotifyClientError as exc:
        error_msg = str(exc)
        # Check if this is an expired/revoked token error
        if "authorization has expired" in error_msg.lower() or "reconnect" in error_msg.lower():
            await _send_link_prompt(message, settings)
        else:
            await message.answer(f"Spotify request failed: {error_msg}")
        return

    if not playback:
        await message.answer(
            "Nothing is playing right now.",
            reply_markup=build_playback_keyboard(),
        )
        return

    await message.answer(
        _format_track(playback),
        reply_markup=build_playback_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=False,
    )


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    text = (
        "🎵 <b>Spotify AI Bot Commands</b>\n\n"
        "<b>Playback Controls:</b>\n"
        "• <code>/now</code> - Show current track and playback controls\n"
        "• Use inline buttons to play, pause, skip, or rewind\n\n"
        "<b>AI Playlist Generation:</b>\n"
        "• <code>/mix &lt;your vibe&gt;</code> - Create a custom playlist\n"
        "  Examples:\n"
        "  • <code>/mix chill coding music</code>\n"
        "  • <code>/mix sunset rooftop vibes</code>\n"
        "  • <code>/mix workout energy</code>\n"
        "  • <code>/mix dreamy vitamin d</code>\n\n"
        "<b>Other:</b>\n"
        "• <code>/start</code> - Restart the bot and connect Spotify\n"
        "• <code>/help</code> - Show this message\n\n"
        "Note: Make sure your Spotify account is connected via <code>/start</code>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=build_playback_keyboard())


@router.message(Command("stats"))
async def handle_stats(message: Message) -> None:
    """Admin-only command to view bot statistics."""
    if message.from_user is None:
        return

    settings = _get_settings(message)

    # Check if user is administrator
    if settings.administrator_user_id is None:
        await message.answer("❌ Administrator access is not configured.")
        return

    if message.from_user.id != settings.administrator_user_id:
        await message.answer("❌ Access denied. This command is for administrators only.")
        return

    # Fetch statistics
    async with repository.connect(settings.db_path) as connection:
        await schema.apply_schema(connection)
        bot_stats = await repository.get_bot_stats(connection)
        recent_users = await repository.get_recent_users(connection, limit=10)
        await connection.commit()

    # Format overview
    text = (
        "📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total Users: <b>{bot_stats.total_users}</b>\n"
        f"🎵 Users with Spotify: <b>{bot_stats.users_with_spotify}</b>\n"
        f"🔀 Total /mix Requests: <b>{bot_stats.total_mix_requests}</b>\n"
        f"🔥 Active Users Today: <b>{bot_stats.users_today}</b>\n\n"
        "👤 <b>Latest 10 Users:</b>\n"
    )

    # Format recent users
    for idx, user in enumerate(recent_users, 1):
        spotify_status = "✅" if user.has_spotify else "❌"
        name = user.first_name or user.username or f"ID:{user.telegram_id}"
        mix_info = f"🎵 {user.total_mix_requests}" if user.total_mix_requests > 0 else ""
        last_request = f" (last: {user.last_request_date})" if user.last_request_date else ""
        text += f"{idx}. {spotify_status} {name} {mix_info}{last_request}\n"

    await message.answer(text, parse_mode="HTML")


__all__ = ["handle_help", "handle_now_playing", "handle_start", "handle_stats", "router"]
