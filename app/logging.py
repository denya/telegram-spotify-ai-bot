"""Logging utilities for the Telegram Spotify bot."""

from __future__ import annotations

import logging
from typing import Final


_DEFAULT_LEVEL: Final[int] = logging.INFO
_DEFAULT_FORMAT: Final[str] = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DEFAULT_DATEFMT: Final[str] = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = _DEFAULT_LEVEL) -> None:
    """Configure root logging if no handlers are present."""

    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(level)
        return

    logging.basicConfig(level=level, format=_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT)


__all__ = ["setup_logging"]
