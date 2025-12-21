"""Generate Spotify playlist ideas using Anthropic Claude."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from ..config import load_settings
from .claude_client import ClaudeConfigurationError, get_client

logger = logging.getLogger(__name__)

MAX_TRACKS = 25
MAX_OUTPUT_TOKENS = 4096  # Increased to accommodate web search results
TEMPERATURE = 0.7

# Web search tool definition for up-to-date music information
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,  # Limit searches to control costs
}

SYSTEM_PROMPT = """You are an expert music curator with deep knowledge of music across all genres,
eras, and cultures. Your specialty is creating highly personalized, contextually perfect
playlists that go beyond surface-level associations.

You have access to web search to find information about recent music releases, current charts,
trending songs, and new artists. Use it when the request involves recent or current music trends.

Core Principles:
1. ANALYZE EVERY WORD in the user's request - each word contributes to the intended mood,
   setting, and emotional tone
2. The LANGUAGE and PHRASING matter - formal vs. casual, poetic vs. direct, all indicate
   different musical directions
3. Avoid obvious, overplayed songs unless they truly fit the specific nuanced request
4. Consider the user's actual listening preferences to ensure recommendations match their taste
5. Think about the complete experience: tempo progression, emotional arc, and thematic
   coherence
6. For requests about recent/new music, use web search to find current releases and trends

Always respond with ONLY a simple list of songs in 'artist - song' format, one per line.
No explanations, no numbering, no markdown."""

USER_PROMPT_TEMPLATE = """Create a playlist of exactly 25 songs based on the following information.

=== USER REQUEST ===
{context}

CRITICAL: Analyze EVERY word in this request. Each word shapes the mood, setting, activity,
time of day, emotional state, and cultural context. Do NOT just pick songs with the main
keyword in the title or lyrics. Instead, capture the complete essence of what the user is
asking for.

{user_preferences}

=== YOUR TASK ===
Curate 25 songs that:
1. Match the COMPLETE meaning and nuance of the request, not just keywords
2. Align with the user's demonstrated music taste and preferences
3. Flow well together as a cohesive listening experience
4. Balance familiarity (from their preferences) with discovery (new artists they'll likely enjoy)
5. Avoid generic, overplayed choices unless they're genuinely perfect for this specific request

Consider:
- The specific mood, setting, and activity described
- Time of day implications (if any)
- Emotional tone and energy level
- Cultural or linguistic nuances in the phrasing
- Whether this is for background ambiance, active listening, or a specific activity

Return ONLY a plain text list with each line in the format: artist - song
Do not add explanations, numbering, markdown, or any other text.

Example format:
The Beatles - Hey Jude
Pink Floyd - Comfortably Numb"""


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

    # Accept any reasonable number of tracks (at least half of requested)
    min_acceptable = MAX_TRACKS // 2
    if len(planned) < min_acceptable:
        raise PlaylistPlannerError(
            f"Claude returned too few tracks: {len(planned)}. Expected at least {min_acceptable}"
        )

    return PlaylistPlan(tracks=planned)


def _resolve_model(model: str | None) -> str:
    resolved = (model or load_settings().anthropic_model).strip()
    if not resolved:
        raise ClaudeConfigurationError("Anthropic model must be configured.")
    return resolved


class ClaudePlaylistPlanner:
    """High-level helper to request playlist ideas from Anthropic Claude."""

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

    async def plan(self, *, context: str, user_preferences: str | None = None) -> PlaylistPlan:
        if not context.strip():
            raise PlaylistPlannerError("Context prompt must not be empty")

        logger.info("Requesting playlist from Claude with context: %s", context[:100])
        logger.debug(
            "Using model: %s, temperature: %s, max_tokens: %s",
            self._model,
            TEMPERATURE,
            MAX_OUTPUT_TOKENS,
        )

        # Build user preferences section
        prefs_section = ""
        if user_preferences and user_preferences.strip():
            prefs_section = f"=== USER'S MUSIC PREFERENCES ===\n{user_preferences.strip()}\n"
        else:
            prefs_section = (
                "=== USER'S MUSIC PREFERENCES ===\n"
                "No preference data available. Focus on the request itself.\n"
            )

        try:
            user_content = USER_PROMPT_TEMPLATE.format(
                context=context.strip(), user_preferences=prefs_section
            )
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=TEMPERATURE,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                tools=[WEB_SEARCH_TOOL],
            )
            logger.info("Received response from Claude API (stop_reason=%s)", response.stop_reason)
        except Exception as exc:
            logger.error("Claude API request failed: %s", exc, exc_info=True)
            raise PlaylistPlannerError(f"Claude API request failed: {exc}") from exc

        if not response.content:
            logger.error("Claude returned empty content")
            raise PlaylistPlannerError("Claude returned an empty response")

        # Find the text block in the response (may have multiple blocks with web search)
        text: str = ""
        for block in response.content:
            block_type = getattr(block, "type", None) or (
                block.get("type") if isinstance(block, dict) else None
            )
            logger.debug("Processing response block: type=%s", block_type)

            if block_type == "text":
                if hasattr(block, "text"):
                    text = block.text
                elif isinstance(block, dict):
                    text = str(block.get("text", ""))
                break  # Use the first text block

        if not text:
            logger.error("No text block found in response, blocks: %s", response.content)
            raise PlaylistPlannerError("Claude response did not contain playlist text")

        logger.debug("Extracted text from response, length: %d", len(text))

        return _parse_tracks(text)


__all__ = [
    "ClaudePlaylistPlanner",
    "PlannedTrack",
    "PlaylistPlan",
    "PlaylistPlannerError",
]
