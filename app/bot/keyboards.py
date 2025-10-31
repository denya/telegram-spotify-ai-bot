"""Telegram inline keyboards used by the Spotify bot."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class PlaybackAction(CallbackData, prefix="player"):
    action: str


def build_auth_keyboard(login_url: str) -> InlineKeyboardMarkup:
    """Render a single button that links to the Spotify login flow."""

    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Connect Spotify", url=login_url)
    builder.adjust(1)
    return builder.as_markup()


def build_playback_keyboard() -> InlineKeyboardMarkup:
    """Inline controls for playback operations (prev/play/pause/next)."""

    builder = InlineKeyboardBuilder()
    builder.button(text="⏮", callback_data=PlaybackAction(action="previous").pack())
    builder.button(text="▶️", callback_data=PlaybackAction(action="play").pack())
    builder.button(text="⏸", callback_data=PlaybackAction(action="pause").pack())
    builder.button(text="⏭", callback_data=PlaybackAction(action="next").pack())
    builder.adjust(4)
    return builder.as_markup()


__all__ = ["PlaybackAction", "build_auth_keyboard", "build_playback_keyboard"]
