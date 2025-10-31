"""Generate Spotify playlist ideas using Anthropic Claude."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from .claude_client import ClaudeConfigurationError, get_client

MODEL_NAME = "claude-3-5-haiku-20241022"
MAX_TRACKS = 25
MAX_OUTPUT_TOKENS = 2048
TEMPERATURE = 0.6


SYSTEM_PROMPT = (
    "You are an experienced music curator. Recommend contemporary or classic songs "
    "that match the listener's requested mood or scenario. Always respond with pure "
    "JSON that matches the schema provided."
)

USER_PROMPT_TEMPLATE = (
    "Using the following context, suggest exactly 25 songs that fit the vibe. "
    'Return ONLY strict JSON with the shape: {{"tracks": [{{"title": str, "artist": str}} * 25]}}. '
    "Do not add explanations, comments, markdown, or keys besides 'tracks'.\n\n"
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


def _extract_json(text: str) -> str:
    text = text.strip()
    fenced = re.match(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text


def _parse_tracks(raw: str) -> PlaylistPlan:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - validation layer
        raise PlaylistPlannerError("Claude response was not valid JSON") from exc

    if not isinstance(payload, dict) or "tracks" not in payload:
        raise PlaylistPlannerError("Claude response missing 'tracks' key")

    tracks_data = payload["tracks"]
    if not isinstance(tracks_data, Sequence) or isinstance(tracks_data, (str, bytes)):
        raise PlaylistPlannerError("'tracks' must be a list of objects")

    planned: list[PlannedTrack] = []
    for entry in tracks_data[:MAX_TRACKS]:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).strip()
        artist = str(entry.get("artist", "")).strip()
        if title and artist:
            planned.append(PlannedTrack(title=title, artist=artist))

    if len(planned) < MAX_TRACKS:
        raise PlaylistPlannerError("Claude returned fewer tracks than requested")

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

        response = await self._client.messages.create(
            model=MODEL_NAME,
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=TEMPERATURE,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": USER_PROMPT_TEMPLATE.format(context=context.strip())}
            ],
        )

        if not response.content:
            raise PlaylistPlannerError("Claude returned an empty response")

        first_block = response.content[0]
        text: str
        if isinstance(first_block, dict):
            text = str(first_block.get("text", ""))
        else:
            text = str(first_block)

        cleaned = _extract_json(text)
        return _parse_tracks(cleaned)


__all__ = [
    "ClaudePlaylistPlanner",
    "PlannedTrack",
    "PlaylistPlan",
    "PlaylistPlannerError",
]
