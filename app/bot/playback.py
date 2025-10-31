"""Handlers for playback inline controls."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery

from ..spotify.client import SpotifyClientError
from .commands import (
    _format_track,
    _get_settings,
    _get_spotify_client,
    _load_tokens_by_telegram_id,
    _send_link_prompt_for_user,
)
from .keyboards import PlaybackAction, build_playback_keyboard

router = Router(name="playback")


_ACTION_TEXT = {
    "play": "Playing",
    "pause": "Paused",
    "next": "Skipped",
    "previous": "Rewound",
}


@router.callback_query(PlaybackAction.filter())
async def handle_playback_callback(callback: CallbackQuery, callback_data: PlaybackAction) -> None:
    message = callback.message
    user = callback.from_user
    await callback.answer()

    if message is None or user is None:
        return

    settings = _get_settings(message)
    tokens = await _load_tokens_by_telegram_id(message, user.id)

    if tokens is None:
        await _send_link_prompt_for_user(
            message,
            settings,
            user.id,
            user.username or "",
            user.first_name or "",
            user.last_name or "",
        )
        return

    spotify = _get_spotify_client(message)
    action = callback_data.action

    try:
        if action == "play":
            await spotify.play(user.id)
        elif action == "pause":
            await spotify.pause(user.id)
        elif action == "next":
            await spotify.next_track(user.id)
        elif action == "previous":
            await spotify.previous_track(user.id)
        else:
            # Unknown action, already answered callback
            return
    except SpotifyClientError as exc:
        await message.answer(f"Spotify request failed: {exc}")
        return

    playback = None
    try:
        playback = await spotify.get_currently_playing(user.id)
    except SpotifyClientError as exc:
        await message.answer(f"Unable to fetch now playing: {exc}")

    if playback:
        await message.answer(
            _format_track(playback),
            reply_markup=build_playback_keyboard(),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "No track information available.",
            reply_markup=build_playback_keyboard(),
        )


__all__ = ["handle_playback_callback", "router"]
