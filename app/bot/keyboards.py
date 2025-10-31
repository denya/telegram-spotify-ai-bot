"""Telegram inline keyboards used by the Spotify bot."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class PlaybackAction(CallbackData, prefix="player"):
    action: str


class TransferConfirm(CallbackData, prefix="transfer"):
    action: str  # play | pause | next | previous
    confirm: str  # yes | no


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


def build_transfer_confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    """Ask the user to confirm transfer before executing action."""

    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Transfer and continue",
        callback_data=TransferConfirm(action=action, confirm="yes").pack(),
    )
    builder.button(
        text="❌ Cancel",
        callback_data=TransferConfirm(action=action, confirm="no").pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


__all__ = [
    "PlaybackAction",
    "TransferConfirm",
    "build_auth_keyboard",
    "build_playback_keyboard",
    "build_transfer_confirm_keyboard",
]
