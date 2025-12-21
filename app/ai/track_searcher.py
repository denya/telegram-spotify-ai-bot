"""Search for tracks using Anthropic Claude to interpret user descriptions."""

from __future__ import annotations

import logging

from anthropic import AsyncAnthropic

from ..config import load_settings
from .claude_client import ClaudeConfigurationError, get_client
from .playlist_planner import PlannedTrack

logger = logging.getLogger(__name__)

MAX_TRACKS = 5
MIN_TRACKS = 1
MAX_OUTPUT_TOKENS = 512
TEMPERATURE = 0.7

SYSTEM_PROMPT = """You are an expert music search engine with comprehensive knowledge of songs
across all languages, genres, eras, and cultures - including English, Russian, Spanish, French,
Japanese, Korean, and all other languages.

Your PRIMARY task is to identify songs from user descriptions. User input may contain:
- Exact or partial LYRICS (even phonetic transcriptions like "ойляля" or "lalala")
- Song themes, emotions, or storylines
- Artist names, genres, or time periods
- Cultural references or contexts

CRITICAL: LYRICS are the strongest signal. If the user provides words that sound like lyrics
(repeated syllables, phrases, recognizable patterns), prioritize finding songs with those EXACT
or similar lyrics, regardless of language.

Response Format:
1. If you can identify ONE specific song with high confidence → return ONLY that track
2. If multiple songs (3-5) could match → return all plausible matches
3. Consider songs from ALL languages and regions equally
4. For lyric-based searches, prefer exact lyric matches over thematic similarities

Always respond with ONLY a simple list in 'artist - song' format, one per line.
No explanations, no numbering, no markdown."""

USER_PROMPT_TEMPLATE = """Find songs matching this user description:

"{description}"

SEARCH STRATEGY:
1. First, check if this contains LYRICS (words, syllables, phrases that appear in songs)
   - Pay special attention to repeated sounds/words (e.g., "ойляля", "lalala", "na-na")
   - Consider phonetic similarities and transliterations
   - Search across ALL languages (English, Russian, Spanish, French, Japanese, etc.)

2. If lyrics found: Return songs containing those EXACT or similar lyrics
3. If no clear lyrics: Return songs matching the theme, mood, or description

OUTPUT:
- Format: artist - song (one per line)
- Return 1 track if confident, 3-5 tracks if multiple possibilities
- Maximum 5 tracks
- No explanations, no numbering, no markdown

IMPORTANT: For lyric searches, be thorough - many users search for songs they remember by
specific words or sounds, especially from non-English songs or children's songs."""


class TrackSearcherError(RuntimeError):
    """Raised when the track searcher fails to produce a valid result."""


def _parse_tracks(raw: str) -> list[PlannedTrack]:
    """Parse simple 'artist - song' format from Claude response."""
    logger.info("Parsing track search response: %s characters", len(raw))
    logger.debug("Raw response: %s", raw[:500])

    text = raw.strip()
    # Remove markdown code blocks if present
    if text.startswith("```") and text.endswith("```"):
        logger.debug("Removing markdown code block wrapper")
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])

    tracks: list[PlannedTrack] = []
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
            tracks.append(PlannedTrack(title=title, artist=artist))
            logger.debug("Parsed track %d: %s - %s", len(tracks), artist, title)
        else:
            logger.warning("Line %d: Empty artist or title: %s", line_num, line)

        if len(tracks) >= MAX_TRACKS:
            break

    logger.info("Successfully parsed %d tracks from Claude response", len(tracks))

    if not tracks:
        raise TrackSearcherError("Could not parse any tracks from Claude response")

    if len(tracks) > MAX_TRACKS:
        logger.warning("Received %d tracks, limiting to %d", len(tracks), MAX_TRACKS)
        tracks = tracks[:MAX_TRACKS]

    return tracks


def _resolve_model(model: str | None) -> str:
    resolved = (model or load_settings().anthropic_model).strip()
    if not resolved:
        raise ClaudeConfigurationError("Anthropic model must be configured.")
    return resolved


class ClaudeTrackSearcher:
    """High-level helper to search for tracks using Anthropic Claude."""

    def __init__(
        self,
        client: AsyncAnthropic | None = None,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            self._client = get_client(api_key)
        else:
            if api_key is not None:
                raise ClaudeConfigurationError("Provide either a client or an API key, not both.")
            self._client = client
        self._model = _resolve_model(model)

    async def search(self, *, description: str) -> list[PlannedTrack]:
        """Search for tracks matching the given description."""
        if not description.strip():
            raise TrackSearcherError("Description must not be empty")

        logger.info("Searching for tracks with description: %s", description[:100])
        logger.debug(
            "Using model: %s, temperature: %s, max_tokens: %s",
            self._model,
            TEMPERATURE,
            MAX_OUTPUT_TOKENS,
        )

        try:
            user_content = USER_PROMPT_TEMPLATE.format(description=description.strip())
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=TEMPERATURE,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            logger.info("Received response from Claude API")
        except Exception as exc:
            logger.error("Claude API request failed: %s", exc, exc_info=True)
            raise TrackSearcherError(f"Claude API request failed: {exc}") from exc

        if not response.content:
            logger.error("Claude returned empty content")
            raise TrackSearcherError("Claude returned an empty response")

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
    "ClaudeTrackSearcher",
    "TrackSearcherError",
]
