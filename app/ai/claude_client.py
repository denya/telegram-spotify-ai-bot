"""Utilities to configure an Anthropic Claude client."""

from __future__ import annotations

from functools import lru_cache

from anthropic import AsyncAnthropic


class ClaudeConfigurationError(RuntimeError):
    """Raised when Anthropic client configuration is invalid."""


@lru_cache(maxsize=1)
def get_client(api_key: str | None) -> AsyncAnthropic:
    """Return a cached AsyncAnthropic client configured with the given API key."""

    if api_key is None or not api_key.strip():
        raise ClaudeConfigurationError("ANTHROPIC_API_KEY must be configured to use Claude.")
    return AsyncAnthropic(api_key=api_key.strip())


__all__ = ["ClaudeConfigurationError", "get_client"]
