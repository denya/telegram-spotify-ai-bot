"""Logging utilities for the Telegram Spotify bot."""

from __future__ import annotations

import logging
import os
from typing import Final

_DEFAULT_LEVEL: Final[int] = logging.INFO
_DEFAULT_FORMAT: Final[str] = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DEFAULT_DATEFMT: Final[str] = "%Y-%m-%d %H:%M:%S"

_LEVEL_MAP: Final[dict[str, int]] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _get_log_level() -> int:
    """Get log level from LOG_LEVEL environment variable."""
    level_str = os.getenv("LOG_LEVEL", "").strip().upper()
    if not level_str:
        return _DEFAULT_LEVEL
    if level_str in _LEVEL_MAP:
        return _LEVEL_MAP[level_str]
    # Try to parse as integer
    try:
        return int(level_str)
    except ValueError:
        return _DEFAULT_LEVEL


def setup_logging(level: int | None = None) -> None:
    """Configure root logging if no handlers are present.

    Args:
        level: Log level to use. If None, reads from LOG_LEVEL env var,
               falling back to INFO.
    """
    if level is None:
        level = _get_log_level()

    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(level)
        return

    logging.basicConfig(level=level, format=_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT)


__all__ = ["setup_logging"]
