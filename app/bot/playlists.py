"""Playlist generation commands using Anthropic Claude and Spotify."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..ai.playlist_planner import (
    ClaudePlaylistPlanner,
    PlannedTrack,
    PlaylistPlannerError,
)
from ..db import repository, schema
from ..spotify.client import SpotifyClientError
from .commands import _get_settings, _get_spotify_client, _load_tokens, _send_link_prompt
from .keyboards import build_playback_keyboard

logger = logging.getLogger(__name__)

router = Router(name="playlists")


async def _fetch_user_preferences(spotify, user_id: int) -> str:
    """Fetch and format user's Spotify preferences for the AI prompt."""
    logger.info("Fetching user preferences for user %s", user_id)

    preferences_parts = []

    try:
        # Fetch multiple time ranges in parallel for a comprehensive view
        (
            top_artists_medium,
            top_artists_long,
            top_tracks_recent,
            recently_played,
        ) = await asyncio.gather(
            spotify.get_top_artists(user_id, time_range="medium_term", limit=15),
            spotify.get_top_artists(user_id, time_range="long_term", limit=10),
            spotify.get_top_tracks(user_id, time_range="short_term", limit=10),
            spotify.get_recently_played(user_id, limit=20),
            return_exceptions=True,
        )

        # Process top artists (medium term - last 6 months)
        if not isinstance(top_artists_medium, Exception) and top_artists_medium:
            artist_names = []
            genres = set()
            for artist in top_artists_medium[:15]:
                if isinstance(artist, dict):
                    name = artist.get("name")
                    if name:
                        artist_names.append(name)
                    artist_genres = artist.get("genres", [])
                    if isinstance(artist_genres, list):
                        genres.update(artist_genres[:3])  # Top 3 genres per artist

            if artist_names:
                preferences_parts.append(
                    f"Favorite Artists (last 6 months): {', '.join(artist_names[:10])}"
                )
            if genres:
                # Limit to most relevant genres
                genre_list = sorted(list(genres))[:12]
                preferences_parts.append(f"Preferred Genres: {', '.join(genre_list)}")

        # Process long-term favorites for deeper understanding
        if not isinstance(top_artists_long, Exception) and top_artists_long:
            long_term_artists = []
            for artist in top_artists_long[:5]:
                if isinstance(artist, dict):
                    name = artist.get("name")
                    if name:
                        long_term_artists.append(name)
            if long_term_artists:
                preferences_parts.append(f"All-Time Favorites: {', '.join(long_term_artists)}")

        # Process recent top tracks
        if not isinstance(top_tracks_recent, Exception) and top_tracks_recent:
            recent_tracks = []
            for track in top_tracks_recent[:8]:
                if isinstance(track, dict):
                    track_name = track.get("name")
                    artists = track.get("artists", [])
                    if track_name and isinstance(artists, list) and artists:
                        artist_name = (
                            artists[0].get("name") if isinstance(artists[0], dict) else None
                        )
                        if artist_name:
                            recent_tracks.append(f"{artist_name} - {track_name}")
            if recent_tracks:
                preferences_parts.append(
                    "Recently Loved Tracks:\n  " + "\n  ".join(recent_tracks[:6])
                )

        # Process recently played to understand current listening patterns
        if not isinstance(recently_played, Exception) and recently_played:
            recent_artists = []
            for item in recently_played[:15]:
                if isinstance(item, dict):
                    track = item.get("track", {})
                    if isinstance(track, dict):
                        artists = track.get("artists", [])
                        if isinstance(artists, list) and artists:
                            artist_name = (
                                artists[0].get("name") if isinstance(artists[0], dict) else None
                            )
                            if artist_name and artist_name not in recent_artists:
                                recent_artists.append(artist_name)
            if recent_artists:
                preferences_parts.append(
                    f"Recently Played Artists: {', '.join(recent_artists[:8])}"
                )

        if preferences_parts:
            result = "\n".join(preferences_parts)
            logger.info("Successfully built user preferences context (%d chars)", len(result))
            return result
        else:
            logger.warning("No preference data could be extracted for user %s", user_id)
            return ""

    except Exception as exc:
        logger.warning("Failed to fetch user preferences: %s", exc, exc_info=True)
        return ""


def _playlist_name(context: str) -> str:
    trimmed = context.strip()
    base = trimmed[:64].strip() or "Custom Mix"
    return f"Mix - {base}"


def _playlist_description(context: str) -> str:
    return f"Autogenerated via Claude for: {context.strip()[:200]}"


def _find_best_uri(options: list[dict], track: PlannedTrack) -> str | None:
    """Find the best matching Spotify URI from search results."""
    if not options:
        return None

    target_title = track.title.lower()
    target_artist = track.artist.lower()

    # First pass: exact title match with artist match
    for candidate in options:
        if not isinstance(candidate, dict):
            continue
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
            return uri

    # Second pass: partial title match with artist match
    for candidate in options:
        if not isinstance(candidate, dict):
            continue
        uri = candidate.get("uri")
        name = str(candidate.get("name", "")).lower()
        artists = [
            str(artist.get("name", "")).lower()
            for artist in candidate.get("artists", [])
            if isinstance(artist, dict)
        ]
        # Check if key words from title are in the track name
        title_words = set(target_title.split())
        name_words = set(name.split())
        if (
            isinstance(uri, str)
            and len(title_words & name_words) >= 2
            and any(target_artist in artist for artist in artists)
        ):
            return uri

    # Third pass: just return the first result if available
    for candidate in options:
        uri = candidate.get("uri") if isinstance(candidate, dict) else None
        if isinstance(uri, str):
            return uri
    return None


def _summarize_tracks(tracks: list[PlannedTrack], limit: int = 10) -> str:
    lines = [
        f"{idx + 1}. {track.title} — {track.artist}" for idx, track in enumerate(tracks[:limit])
    ]
    if len(tracks) > limit:
        lines.append("…")
    return "\n".join(lines)


async def _search_track_with_retry(
    spotify,
    user_id: int,
    planned: PlannedTrack,
    semaphore: asyncio.Semaphore,
) -> tuple[PlannedTrack, str | None]:
    """Search for a single track with rate limiting."""
    query = f"{planned.artist} {planned.title}"
    async with semaphore:
        try:
            logger.debug("Searching: %s", query)
            results = await spotify.search_track(user_id, query=query, limit=5)
            uri = _find_best_uri(results, planned) if results else None
            if uri is None:
                logger.warning(
                    "Could not find Spotify URI for: %s - %s", planned.artist, planned.title
                )
            else:
                logger.debug("Found URI for track: %s", uri)
            return (planned, uri)
        except SpotifyClientError as exc:
            logger.error("Spotify search failed for query '%s': %s", query, exc)
            raise


async def _search_tracks_parallel(
    spotify,
    user_id: int,
    tracks: list[PlannedTrack],
    *,
    max_concurrent: int = 5,
    batch_size: int = 10,
    status_message=None,
) -> tuple[list[tuple[PlannedTrack, str]], list[PlannedTrack]]:
    """Search for tracks in parallel with rate limiting and batching."""
    found_tracks: list[tuple[PlannedTrack, str]] = []
    missing_tracks: list[PlannedTrack] = []

    # Process in batches to avoid overwhelming the API
    semaphore = asyncio.Semaphore(max_concurrent)

    for batch_idx in range(0, len(tracks), batch_size):
        batch = tracks[batch_idx : batch_idx + batch_size]
        batch_end = min(batch_idx + batch_size, len(tracks))
        logger.info(
            "Processing batch %d-%d of %d tracks",
            batch_idx + 1,
            batch_end,
            len(tracks),
        )

        # Update progress message
        if status_message is not None:
            try:
                await status_message.edit_text(
                    f"Cooking up a playlist… ({batch_end}/{len(tracks)} tracks)"
                )
            except Exception as exc:
                logger.debug("Could not update progress message: %s", exc)

        # Create tasks for this batch
        tasks = [
            _search_track_with_retry(spotify, user_id, planned, semaphore) for planned in batch
        ]

        # Wait for all tasks in this batch to complete
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as exc:
            logger.error("Batch search failed: %s", exc)
            raise SpotifyClientError(f"Search batch failed: {exc}") from exc

        # Process results
        for result in results:
            if isinstance(result, Exception):
                # If any search in the batch failed, propagate the error
                raise result
            planned, uri = result
            if uri is None:
                missing_tracks.append(planned)
            else:
                found_tracks.append((planned, uri))

        # Small delay between batches to be nice to the API
        if batch_idx + batch_size < len(tracks):
            await asyncio.sleep(0.5)

    return found_tracks, missing_tracks


@router.message(Command("mix"))
async def handle_mix_command(message: Message) -> None:
    settings = _get_settings(message)
    tokens = await _load_tokens(message)

    if message.from_user is None:
        await message.answer("I couldn't detect your Telegram account.")
        return

    user_id = message.from_user.id
    logger.info("User %s requested /mix command", user_id)

    if tokens is None:
        logger.warning("User %s not authenticated with Spotify", user_id)
        await _send_link_prompt(message, settings)
        return

    if not settings.anthropic_api_key:
        logger.error("Anthropic API key not configured")
        await message.answer("Anthropic API key is not configured on the server.")
        return

    if message.text is None:
        await message.answer("Provide some context, e.g. /mix dreamy evening coding")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Tell me what vibe you want, for example: /mix sunset rooftop vibes")
        return

    context = parts[1].strip()
    logger.info("User %s requesting playlist with context: %s", user_id, context)

    now = datetime.now(tz=UTC)
    internal_user_id: int | None = None
    rate_limit_request_date: str | None = None
    processing_marked = False
    status = None

    try:
        async with repository.connect(settings.db_path) as connection:
            await schema.apply_schema(connection)
            await connection.commit()
            await connection.execute("BEGIN IMMEDIATE")
            profile = repository.UserProfile(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
            )
            try:
                internal_user_id = await repository.ensure_user(connection, profile)
                rate_limit = await repository.check_mix_rate_limit(
                    connection,
                    user_id=internal_user_id,
                    now=now,
                )
                if not rate_limit.allowed:
                    await connection.rollback()
                    await message.answer(rate_limit.reason or "Too many mixes right now, bro.")
                    return
                await repository.increment_mix_request(
                    connection,
                    user_id=internal_user_id,
                    request_date=rate_limit.request_date,
                    now=now,
                )
                await repository.mark_mix_processing(
                    connection,
                    user_id=internal_user_id,
                    request_date=rate_limit.request_date,
                    now=now,
                )
                await connection.commit()
                rate_limit_request_date = rate_limit.request_date
                processing_marked = True
            except Exception:
                await connection.rollback()
                raise
    except Exception:
        logger.exception("Rate limiting failed for user %s", user_id)
        await message.answer("Couldn't start the mix right now. Try again in a bit.")
        return

    try:
        status = await message.answer("Cooking up a playlist…")

        spotify = _get_spotify_client(message)

        # Fetch user preferences to personalize the playlist
        logger.info("Fetching user preferences for personalization")
        try:
            user_preferences = await _fetch_user_preferences(spotify, user_id)
            if user_preferences:
                logger.info(
                    "User preferences fetched successfully, %d chars", len(user_preferences)
                )
            else:
                logger.info("No user preferences available, will use request only")
        except Exception as exc:
            logger.warning("Failed to fetch user preferences, continuing without: %s", exc)
            user_preferences = ""

        planner = ClaudePlaylistPlanner(api_key=settings.anthropic_api_key)
        try:
            plan = await planner.plan(context=context, user_preferences=user_preferences)
            logger.info("Successfully generated playlist plan with %d tracks", len(plan.tracks))
        except PlaylistPlannerError as exc:
            logger.error("Playlist planning failed for user %s: %s", user_id, exc, exc_info=True)
            await status.edit_text(f"Claude couldn't build a playlist: {exc}")
            return

        logger.info("Starting parallel Spotify search for %d tracks", len(plan.tracks))
        try:
            found_tracks, missing_tracks = await _search_tracks_parallel(
                spotify,
                user_id,
                plan.tracks,
                max_concurrent=5,
                batch_size=10,
                status_message=status,
            )
        except SpotifyClientError as exc:
            logger.error("Parallel search failed: %s", exc)
            await status.edit_text(f"Spotify search failed: {exc}")
            return

        logger.info(
            "Spotify search complete: %d found, %d missing", len(found_tracks), len(missing_tracks)
        )

        if not found_tracks:
            logger.error("No tracks found on Spotify for any suggestions")
            await status.edit_text("Couldn't match any of the suggested songs on Spotify.")
            return

        playlist_name = _playlist_name(context)
        playlist_description = _playlist_description(context)

        logger.info("Creating Spotify playlist: %s", playlist_name)
        try:
            playlist = await spotify.create_playlist(
                user_id,
                name=playlist_name,
                description=playlist_description,
                public=False,
            )
            logger.info("Playlist created successfully")
        except SpotifyClientError as exc:
            logger.error("Failed to create playlist: %s", exc)
            await status.edit_text(f"Failed to create playlist: {exc}")
            return

        playlist_id = playlist.get("id") if isinstance(playlist, dict) else None
        playlist_url = None
        if isinstance(playlist, dict):
            external = playlist.get("external_urls")
            if isinstance(external, dict):
                playlist_url = external.get("spotify")

        if not isinstance(playlist_id, str):
            logger.error("Spotify did not return a valid playlist ID")
            await status.edit_text("Spotify did not return a playlist identifier.")
            return

        logger.info("Adding %d tracks to playlist %s", len(found_tracks), playlist_id)
        try:
            await spotify.add_tracks(
                user_id,
                playlist_id=playlist_id,
                track_uris=[uri for _, uri in found_tracks],
            )
            logger.info("Successfully added tracks to playlist")
        except SpotifyClientError as exc:
            logger.error("Failed to add tracks to playlist: %s", exc)
            await status.edit_text(f"Unable to add tracks: {exc}")
            return

        summary = _summarize_tracks([track for track, _ in found_tracks])
        message_lines = [
            "✅ Playlist created!",
            f"Name: <b>{playlist_name}</b>",
        ]
        if playlist_url:
            message_lines.append(f"Link: <a href='{playlist_url}'>{playlist_url}</a>")
        message_lines.append("\nTop picks:\n" + summary)

        if missing_tracks:
            missed_summary = ", ".join(
                f"{track.title} — {track.artist}" for track in missing_tracks[:5]
            )
            message_lines.append(f"\nCouldn't find: {missed_summary}")
            logger.info(
                "Playlist created with %d tracks, %d tracks could not be found",
                len(found_tracks),
                len(missing_tracks),
            )
        else:
            logger.info("Playlist created successfully with all %d tracks", len(found_tracks))

        await status.edit_text(
            "\n".join(message_lines),
            parse_mode="HTML",
            disable_web_page_preview=False,
            reply_markup=build_playback_keyboard(),
        )
    finally:
        if (
            processing_marked
            and internal_user_id is not None
            and rate_limit_request_date is not None
        ):
            try:
                async with repository.connect(settings.db_path) as connection:
                    await repository.clear_mix_processing(
                        connection,
                        user_id=internal_user_id,
                        request_date=rate_limit_request_date,
                    )
                    await connection.commit()
            except Exception:
                logger.exception("Failed to clear mix processing lock for user %s", user_id)


__all__ = ["handle_mix_command", "router"]
