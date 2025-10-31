"""Generate Spotify playlist ideas using Anthropic Claude."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from .claude_client import ClaudeConfigurationError, get_client

logger = logging.getLogger(__name__)

MODEL_NAME = "claude-3-5-haiku-20241022"
MAX_TRACKS = 25
MAX_OUTPUT_TOKENS = 2048
TEMPERATURE = 0.6


SYSTEM_PROMPT = (
    "You are an experienced music curator. Recommend contemporary or classic songs "
    "that match the listener's requested mood or scenario. Always respond with a simple "
    "list of songs in 'artist - song' format, one per line."
)

USER_PROMPT_TEMPLATE = (
    "Using the following context, suggest exactly 25 songs that fit the vibe. "
    "Return ONLY a plain text list with each line in the format: artist - song\n"
    "Do not add explanations, numbering, markdown, or any other text.\n\n"
    "Example format:\n"
    "The Beatles - Hey Jude\n"
    "Pink Floyd - Comfortably Numb\n\n"
    "Context: {context}"
)


class PlaylistPlannerError(RuntimeError):
    """Raised when the playlist planner fails to produce a valid result."""


@dataclass(slots=True, frozen=True)
class PlannedTrack:
    title: str
    artist: str


@dataclass(slots=True)
class PlaylistPlan:
    tracks: list[PlannedTrack]


def _parse_tracks(raw: str) -> PlaylistPlan:
    """Parse simple 'artist - song' format from Claude response."""
    logger.info("Parsing Claude response: %s characters", len(raw))
    logger.debug("Raw response: %s", raw[:500])  # Log first 500 chars

    text = raw.strip()
    # Remove markdown code blocks if present
    if text.startswith("```") and text.endswith("```"):
        logger.debug("Removing markdown code block wrapper")
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])

    planned: list[PlannedTrack] = []
    for line_num, line in enumerate(text.split("\n"), start=1):
        line = line.strip()
        if not line:
            continue

        # Remove numbering if present (e.g., "1. ", "1) ")
        line = line.lstrip("0123456789.)-• ")

        # Split by " - " to separate artist and song
        parts = line.split(" - ", 1)
        if len(parts) != 2:
            logger.warning("Line %d: Invalid format (no ' - ' separator): %s", line_num, line)
            continue

        artist = parts[0].strip()
        title = parts[1].strip()

        if artist and title:
            planned.append(PlannedTrack(title=title, artist=artist))
            logger.debug("Parsed track %d: %s - %s", len(planned), artist, title)
        else:
            logger.warning("Line %d: Empty artist or title: %s", line_num, line)

        if len(planned) >= MAX_TRACKS:
            break

    logger.info("Successfully parsed %d tracks from Claude response", len(planned))

    if not planned:
        raise PlaylistPlannerError("Could not parse any tracks from Claude response")

    if len(planned) < MAX_TRACKS:
        logger.warning("Only parsed %d tracks, expected %d", len(planned), MAX_TRACKS)

    return PlaylistPlan(tracks=planned)


class ClaudePlaylistPlanner:
    """High-level helper to request playlist ideas from Anthropic Claude."""

    def __init__(self, client: AsyncAnthropic | None = None, *, api_key: str | None = None) -> None:
        if client is None:
            self._client = get_client(api_key)
        else:
            if api_key is not None:
                raise ClaudeConfigurationError("Provide either a client or an API key, not both.")
            self._client = client

    async def plan(self, *, context: str) -> PlaylistPlan:
        if not context.strip():
            raise PlaylistPlannerError("Context prompt must not be empty")

        logger.info("Requesting playlist from Claude with context: %s", context[:100])
        logger.debug(
            "Using model: %s, temperature: %s, max_tokens: %s",
            MODEL_NAME,
            TEMPERATURE,
            MAX_OUTPUT_TOKENS,
        )

        try:
            user_content = USER_PROMPT_TEMPLATE.format(context=context.strip())
            response = await self._client.messages.create(
                model=MODEL_NAME,
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=TEMPERATURE,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            logger.info("Received response from Claude API")
        except Exception as exc:
            logger.error("Claude API request failed: %s", exc, exc_info=True)
            raise PlaylistPlannerError(f"Claude API request failed: {exc}") from exc

        if not response.content:
            logger.error("Claude returned empty content")
            raise PlaylistPlannerError("Claude returned an empty response")

        first_block = response.content[0]
        text: str
        # Extract text from TextBlock object properly
        if hasattr(first_block, "text"):
            text = first_block.text
        elif isinstance(first_block, dict):
            text = str(first_block.get("text", ""))
        else:
            text = str(first_block)

        logger.debug("Response text type: %s, length: %d", type(first_block).__name__, len(text))

        return _parse_tracks(text)


__all__ = [
    "ClaudePlaylistPlanner",
    "PlannedTrack",
    "PlaylistPlan",
    "PlaylistPlannerError",
]
