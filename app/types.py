"""Shared typing primitives for the Telegram Spotify AI Bot."""

from __future__ import annotations

from typing import Literal


TelegramMode = Literal["polling", "webhook"]

__all__ = ["TelegramMode"]
