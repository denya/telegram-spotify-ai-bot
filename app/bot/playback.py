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
from .keyboards import (
    PlaybackAction,
    TransferConfirm,
    build_playback_keyboard,
    build_transfer_confirm_keyboard,
)

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
            await spotify.play(user.id, allow_transfer=False)
        elif action == "pause":
            await spotify.pause(user.id, allow_transfer=False)
        elif action == "next":
            await spotify.next_track(user.id, allow_transfer=False)
        elif action == "previous":
            await spotify.previous_track(user.id, allow_transfer=False)
        else:
            # Unknown action, already answered callback
            return
    except SpotifyClientError as exc:
        error_msg = str(exc)
        # Check if this is an expired/revoked token error
        if "authorization has expired" in error_msg.lower() or "reconnect" in error_msg.lower():
            await _send_link_prompt_for_user(
                message,
                settings,
                user.id,
                user.username or "",
                user.first_name or "",
                user.last_name or "",
            )
        elif "Restricted device" in error_msg or "No controllable device available" in error_msg:
            await message.answer(
                "This device can't be controlled. Transfer playback to a controllable "
                "device (phone/computer) to continue?",
                reply_markup=build_transfer_confirm_keyboard(action),
            )
        else:
            await message.answer(f"❌ Spotify error: {error_msg}")
        return

    playback = None
    try:
        playback = await spotify.get_currently_playing(user.id)
    except SpotifyClientError as exc:
        error_msg = str(exc)
        if "authorization has expired" in error_msg.lower() or "reconnect" in error_msg.lower():
            await _send_link_prompt_for_user(
                message,
                settings,
                user.id,
                user.username or "",
                user.first_name or "",
                user.last_name or "",
            )
        else:
            await message.answer(f"Unable to fetch now playing: {error_msg}")

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


@router.callback_query(TransferConfirm.filter())
async def handle_transfer_confirm(callback: CallbackQuery, callback_data: TransferConfirm) -> None:
    message = callback.message
    user = callback.from_user
    await callback.answer()

    if message is None or user is None:
        return

    spotify = _get_spotify_client(message)
    action = callback_data.action
    confirm = callback_data.confirm

    if confirm != "yes":
        await message.answer("Okay, cancelled.")
        return

    try:
        if action == "play":
            await spotify.play(user.id, allow_transfer=True)
        elif action == "pause":
            await spotify.pause(user.id, allow_transfer=True)
        elif action == "next":
            await spotify.next_track(user.id, allow_transfer=True)
        elif action == "previous":
            await spotify.previous_track(user.id, allow_transfer=True)
        else:
            return
    except SpotifyClientError as exc:
        error_msg = str(exc)
        if "authorization has expired" in error_msg.lower() or "reconnect" in error_msg.lower():
            await _send_link_prompt_for_user(
                message,
                _get_settings(message),
                user.id,
                user.username or "",
                user.first_name or "",
                user.last_name or "",
            )
        else:
            await message.answer(f"❌ Spotify error after transfer: {error_msg}")
        return

    # Show updated now playing
    settings = _get_settings(message)
    try:
        playback = await spotify.get_currently_playing(user.id)
    except SpotifyClientError as exc:
        error_msg = str(exc)
        if "authorization has expired" in error_msg.lower() or "reconnect" in error_msg.lower():
            await _send_link_prompt_for_user(
                message,
                settings,
                user.id,
                user.username or "",
                user.first_name or "",
                user.last_name or "",
            )
        else:
            await message.answer(f"Unable to fetch now playing: {error_msg}")
        return

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
