"""Song search command powered by Claude and Spotify."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..ai.playlist_planner import PlannedTrack
from ..ai.track_searcher import ClaudeTrackSearcher, TrackSearcherError
from ..spotify.client import SpotifyClientError
from .commands import _get_settings, _get_spotify_client, _load_tokens, _send_link_prompt

router = Router(name="search")

logger = logging.getLogger(__name__)


def _select_best_track(
    options: list[dict[str, Any]], planned: PlannedTrack
) -> dict[str, Any] | None:
    """Pick the best Spotify track candidate for the planned track."""

    if not options:
        return None

    target_title = planned.title.lower()
    target_artist = planned.artist.lower()

    # Pass 1: exact title match with artist match
    for candidate in options:
        uri = candidate.get("uri")
        name = str(candidate.get("name", "")).lower()
        artists = [
            str(artist.get("name", "")).lower()
            for artist in candidate.get("artists", [])
            if isinstance(artist, dict)
        ]
        if (
            isinstance(uri, str)
            and target_title in name
            and any(target_artist in artist for artist in artists)
        ):
            return candidate

    # Pass 2: partial title match with artist match
    for candidate in options:
        uri = candidate.get("uri")
        name = str(candidate.get("name", "")).lower()
        artists = [
            str(artist.get("name", "")).lower()
            for artist in candidate.get("artists", [])
            if isinstance(artist, dict)
        ]
        title_words = set(target_title.split())
        name_words = set(name.split())
        if (
            isinstance(uri, str)
            and len(title_words & name_words) >= 2
            and any(target_artist in artist for artist in artists)
        ):
            return candidate

    # Fall back to first option with a URI
    for candidate in options:
        if isinstance(candidate.get("uri"), str):
            return candidate

    return None


def _format_track_message(track: dict[str, Any], *, prefix: str | None = None) -> str:
    name = track.get("name") or "Unknown title"
    artists = ", ".join(
        artist.get("name", "?") for artist in track.get("artists", []) if isinstance(artist, dict)
    )
    album = track.get("album", {}).get("name") if isinstance(track.get("album"), dict) else None
    external_urls = track.get("external_urls")
    url = external_urls.get("spotify") if isinstance(external_urls, dict) else None

    lines: list[str] = []
    if prefix:
        lines.append(prefix)
    lines.append(f"🎶 <b>{name}</b>")
    if artists:
        lines.append(f"by {artists}")
    if album:
        lines.append(f"on {album}")
    text = "\n".join(lines)
    if url:
        text += f"\n<a href='{url}'>Open in Spotify</a>"
    return text


def _format_missing_tracks(missing: list[PlannedTrack]) -> str:
    if not missing:
        return ""
    summary = ", ".join(f"{track.artist} - {track.title}" for track in missing[:5])
    if len(missing) > 5:
        summary += ", ..."
    return f"\n\nCouldn't match on Spotify: {summary}"


@router.message(Command("search"))
async def handle_search_command(message: Message) -> None:
    settings = _get_settings(message)
    tokens = await _load_tokens(message)

    if message.from_user is None:
        await message.answer("I couldn't detect your Telegram account.")
        return

    user_id = message.from_user.id

    if tokens is None:
        await _send_link_prompt(message, settings)
        return

    if not settings.anthropic_api_key:
        await message.answer("Anthropic API key is not configured on the server.")
        return

    if message.text is None:
        await message.answer("Describe the song you're looking for, e.g. /search lalala words")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Please describe the song, e.g. /search lalala words")
        return

    description = parts[1].strip()
    logger.info("User %s triggered /search with description: %s", user_id, description)

    status_message = await message.answer("🎧 Listening to your description...")

    searcher = ClaudeTrackSearcher(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
    )

    try:
        suggestions = await searcher.search(description=description)
    except TrackSearcherError as exc:
        logger.error("Track search failed for user %s: %s", user_id, exc)
        await status_message.edit_text(f"Couldn't interpret your description: {exc!s}")
        return

    spotify = _get_spotify_client(message)

    found_tracks: list[tuple[PlannedTrack, dict[str, Any]]] = []
    missing_tracks: list[PlannedTrack] = []

    for planned in suggestions:
        query = f"{planned.artist} {planned.title}"
        try:
            search_results = await spotify.search_track(user_id, query=query, limit=5)
        except SpotifyClientError as exc:
            logger.error("Spotify track search failed for query '%s': %s", query, exc)
            await status_message.edit_text(f"Spotify search failed: {exc!s}")
            return

        best = _select_best_track(search_results, planned)
        if best is None:
            missing_tracks.append(planned)
            logger.info("No Spotify result for %s - %s", planned.artist, planned.title)
            continue

        found_tracks.append((planned, best))
        logger.info(
            "Matched '%s - %s' to Spotify track '%s'",
            planned.artist,
            planned.title,
            best.get("uri"),
        )

    if not found_tracks:
        await status_message.edit_text(
            "Couldn't find any matching tracks on Spotify. Try refining your description."
        )
        return

    if len(found_tracks) == 1:
        planned, track = found_tracks[0]
        prefix = f"Found a match for <i>{description}</i>:"
        text = _format_track_message(track, prefix=prefix)
        text += _format_missing_tracks(missing_tracks)
        try:
            await status_message.edit_text(
                text,
                parse_mode="HTML",
                disable_web_page_preview=False,
            )
        except Exception:
            await message.answer(text, parse_mode="HTML", disable_web_page_preview=False)
        return

    # Multiple tracks scenario
    try:
        count = len(found_tracks)
        await status_message.edit_text(f"Found {count} possibilities! Sending matches...")
    except Exception as exc:
        logger.debug("Could not update status message before sending matches: %s", exc)

    for index, (_, track) in enumerate(found_tracks, start=1):
        prefix = f"#{index}"
        text = _format_track_message(track, prefix=prefix)
        await message.answer(text, parse_mode="HTML", disable_web_page_preview=False)

    if missing_tracks:
        missing_summary = _format_missing_tracks(missing_tracks)
        await message.answer(missing_summary, parse_mode="HTML")
