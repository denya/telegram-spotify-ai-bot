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
from .keyboards import build_auth_keyboard, build_playback_keyboard

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
    return await token_store.load(message.from_user.id) if message.from_user else None


async def _send_link_prompt(message: Message, settings: Settings) -> None:
    login_url = _build_login_url(settings, message)
    await message.answer(
        "Let's connect your Spotify account first.",
        reply_markup=build_auth_keyboard(login_url),
    )


def _format_track(payload: dict[str, Any]) -> str:
    item = payload.get("item")
    if not isinstance(item, dict):
        return "Nothing is playing right now."
    name = item.get("name") or "Unknown title"
    artists = ", ".join(
        artist.get("name", "?") for artist in item.get("artists", []) if isinstance(artist, dict)
    )
    album = (item.get("album") or {}).get("name") if isinstance(item.get("album"), dict) else None
    external = item.get("external_urls") if isinstance(item.get("external_urls"), dict) else {}
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

    await message.answer(
        "Spotify is linked. Use the controls below to manage playback.",
        reply_markup=build_playback_keyboard(),
    )


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
        await message.answer(f"Spotify request failed: {exc}")
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


__all__ = ["handle_now_playing", "handle_start", "router"]
