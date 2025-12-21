"""Async Spotify Web API client with token refresh support."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

from ..config import Settings
from ..db import repository
from . import auth as spotify_auth
from .auth import RevokedTokenError


class SpotifyClientError(RuntimeError):
    """Raised when Spotify API responses indicate an error."""


@dataclass(slots=True)
class RepositoryTokenStore:
    """Simple wrapper around sqlite-backed token persistence."""

    db_path: Path

    async def load(self, user_id: int) -> repository.SpotifyTokens | None:
        async with repository.connect(self.db_path) as connection:
            return await repository.get_spotify_tokens(connection, user_id)

    async def load_by_telegram_id(self, telegram_id: int) -> repository.SpotifyTokens | None:
        """Load tokens by Telegram user ID, converting to internal user ID first."""
        async with repository.connect(self.db_path) as connection:
            internal_user_id = await repository.get_user_id_by_telegram_id(connection, telegram_id)
            if internal_user_id is None:
                return None
            return await repository.get_spotify_tokens(connection, internal_user_id)

    async def save(
        self,
        user_id: int,
        *,
        access_token: str,
        refresh_token: str | None,
        scope: str,
        token_type: str,
        expires_at: datetime,
    ) -> repository.SpotifyTokens:
        async with repository.connect(self.db_path) as connection:
            await repository.upsert_spotify_tokens(
                connection,
                user_id=user_id,
                access_token=access_token,
                refresh_token=refresh_token,
                scope=scope,
                token_type=token_type,
                expires_at=expires_at,
            )
            await connection.commit()
        tokens = repository.SpotifyTokens(
            user_id=user_id,
            access_token=access_token,
            refresh_token=refresh_token,
            scope=scope,
            token_type=token_type,
            expires_at=expires_at,
        )
        return tokens


def _default_http_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(10.0, read=10.0)
    return httpx.AsyncClient(base_url="https://api.spotify.com/v1", timeout=timeout)


def _sanitize_playlist_name(name: str) -> str:
    """
    Sanitize playlist name to avoid Spotify API 400 errors.

    Spotify has undocumented restrictions on playlist names. This function:
    - Limits length to 100 characters (Spotify's max is around 200)
    - Strips leading/trailing whitespace
    - Replaces problematic characters that may cause encoding issues
    - Ensures the name is not empty
    """
    if not name:
        return "My Playlist"

    # Strip whitespace
    sanitized = name.strip()

    # Limit length (Spotify allows up to ~200 chars, but 100 is safer)
    if len(sanitized) > 100:
        sanitized = sanitized[:100].rsplit(" ", 1)[0] or sanitized[:100]

    # Ensure name is not empty after processing
    if not sanitized:
        return "My Playlist"

    return sanitized


class SpotifyClient:
    """High-level helper around Spotify Web API endpoints."""

    def __init__(
        self,
        *,
        settings: Settings,
        token_store: RepositoryTokenStore,
        http_client_factory: Callable[[], httpx.AsyncClient] = _default_http_client,
    ) -> None:
        self._client_id = settings.spotify_client_id
        self._client_secret = settings.spotify_client_secret
        self._token_store = token_store
        self._http = http_client_factory()
        self._owns_client = True
        self._cache: dict[int, repository.SpotifyTokens] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _get_tokens(self, user_id: int) -> repository.SpotifyTokens:
        tokens = self._cache.get(user_id)
        if tokens is None:
            # user_id is actually telegram_id, load by telegram_id
            tokens = await self._token_store.load_by_telegram_id(user_id)
            if tokens is None:
                raise SpotifyClientError("Spotify not authorized for this user")
            self._cache[user_id] = tokens
        return tokens

    async def _refresh_tokens(
        self, user_id: int, tokens: repository.SpotifyTokens
    ) -> repository.SpotifyTokens:
        refresh_token = tokens.refresh_token
        if refresh_token is None:
            raise SpotifyClientError("No refresh token stored for user")
        try:
            response = await spotify_auth.refresh_access_token(
                client_id=self._client_id,
                client_secret=self._client_secret,
                refresh_token=refresh_token,
            )
        except RevokedTokenError as exc:
            # Clear revoked tokens from database and cache
            async with repository.connect(self._token_store.db_path) as connection:
                await repository.delete_spotify_tokens(connection, tokens.user_id)
                await connection.commit()
            # Clear from cache
            self._cache.pop(user_id, None)
            # Re-raise with user-friendly message
            raise SpotifyClientError(
                "Your Spotify authorization has expired. Please reconnect your account using /start"
            ) from exc
        scope = response.scope or tokens.scope
        token_type = response.token_type or tokens.token_type
        persisted_refresh = response.refresh_token or refresh_token
        expires_at = spotify_auth.compute_expiry(response.expires_in)
        updated = await self._token_store.save(
            tokens.user_id,  # Use internal database user_id, not telegram_id
            access_token=response.access_token,
            refresh_token=persisted_refresh,
            scope=scope,
            token_type=token_type,
            expires_at=expires_at,
        )
        self._cache[user_id] = updated
        return updated

    async def _ensure_fresh_tokens(self, user_id: int) -> repository.SpotifyTokens:
        tokens = await self._get_tokens(user_id)
        if spotify_auth.should_refresh(tokens.expires_at):
            tokens = await self._refresh_tokens(user_id, tokens)
        return tokens

    async def _request(
        self,
        user_id: int,
        method: str,
        path: str,
        *,
        retry: bool = True,
        expected_status: Sequence[int] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            tokens = await self._ensure_fresh_tokens(user_id)
        except SpotifyClientError:
            # If token refresh fails (e.g., revoked), re-raise immediately
            # Don't try to use potentially stale tokens
            raise
        headers = kwargs.pop("headers", {})
        headers.setdefault("Authorization", f"Bearer {tokens.access_token}")
        response = await self._http.request(method, path, headers=headers, **kwargs)

        if response.status_code == 401 and retry:
            try:
                # Clear cache before retry to force reload of fresh tokens
                self._cache.pop(user_id, None)
                # Reload tokens from DB (might have been updated elsewhere)
                fresh_tokens = await self._get_tokens(user_id)
                tokens = await self._refresh_tokens(user_id, fresh_tokens)
                headers["Authorization"] = f"Bearer {tokens.access_token}"
                response = await self._http.request(method, path, headers=headers, **kwargs)
            except SpotifyClientError:
                # If refresh fails (e.g., revoked token), propagate the error
                # Tokens have already been cleared in _refresh_tokens
                raise

        # Treat any 2xx as success. Some Spotify endpoints may return 200
        # even when docs claim 204, so be permissive here.
        if not (200 <= response.status_code < 300):
            # Log full response for debugging
            logger.error(
                "Spotify API error: %s %s -> %d, body: %s",
                method,
                path,
                response.status_code,
                response.text[:1000] if response.text else "(empty)",
            )

            # Try to parse error message for better user-facing errors
            error_message = response.text
            try:
                error_json = response.json()
                if isinstance(error_json, dict):
                    error_obj = error_json.get("error", {})
                    if isinstance(error_obj, dict):
                        status_code = error_obj.get("status")
                        message = error_obj.get("message", "")
                        reason = error_obj.get("reason", "")
                        if message:
                            if status_code == 403 and "Restricted device" in message:
                                error_message = (
                                    "Restricted device: This device cannot be controlled "
                                    "via the API. Please use Spotify on your phone or "
                                    "computer instead."
                                )
                            elif status_code == 403:
                                error_message = f"Access forbidden: {message}"
                            elif status_code == 400:
                                # Log additional details for bad request errors
                                logger.error(
                                    "Bad request details - message: %s, reason: %s",
                                    message,
                                    reason,
                                )
                                error_message = f"Bad request: {message}"
                            else:
                                error_message = message
            except (ValueError, TypeError, AttributeError):
                # Failed to parse JSON, use raw text
                pass

            raise SpotifyClientError(
                f"Spotify request failed ({response.status_code}): {error_message}"
            )
        return response

    async def get_profile(self, user_id: int) -> dict[str, Any]:
        response = await self._request(user_id, "GET", "/me")
        return response.json()  # type: ignore[no-any-return]

    async def get_currently_playing(self, user_id: int) -> dict[str, Any] | None:
        response = await self._request(
            user_id,
            "GET",
            "/me/player/currently-playing",
            expected_status=(200, 204),
        )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()  # type: ignore[no-any-return]

    async def get_player(self, user_id: int) -> dict[str, Any] | None:
        """Get information about the user's current playback state."""
        response = await self._request(
            user_id,
            "GET",
            "/me/player",
            expected_status=(200, 204),
        )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()  # type: ignore[no-any-return]

    async def get_devices(self, user_id: int) -> list[dict[str, Any]]:
        """Get the list of devices available for the user."""
        response = await self._request(user_id, "GET", "/me/player/devices")
        payload = response.json()
        devices = payload.get("devices")
        if not isinstance(devices, list):
            return []
        return devices

    async def transfer_playback(self, user_id: int, *, device_id: str, play: bool = False) -> None:
        """Transfer playback to a specific device."""
        await self._request(
            user_id,
            "PUT",
            "/me/player",
            json={"device_ids": [device_id], "play": play},
            expected_status=(204,),
        )

    async def _ensure_controllable_device(
        self, user_id: int, *, allow_transfer: bool
    ) -> str | None:
        """
        Ensure playback is on a controllable device.
        Returns device_id if successful, None if no controllable device is available.
        """
        try:
            # First, check current player state
            player_state = await self.get_player(user_id)
            if player_state:
                device = player_state.get("device")
                if isinstance(device, dict):
                    device_id = device.get("id")
                    is_restricted = device.get("is_restricted", False)
                    if isinstance(device_id, str) and not is_restricted:
                        # Current device is not restricted, we're good
                        return device_id
        except SpotifyClientError:
            # Player state unavailable, continue to check devices
            pass

        # Current device is restricted or not available
        if not allow_transfer:
            return None

        # Find a controllable one and transfer if allowed
        try:
            devices = await self.get_devices(user_id)

            # Prefer active devices first, then any controllable device
            # Avoid Speaker type devices which might still be restricted
            controllable_devices = []
            active_controllable_devices = []

            for device in devices:
                device_id = device.get("id")
                is_restricted = device.get("is_restricted", False)
                is_active = device.get("is_active", False)
                device_type = device.get("type", "")

                # Prefer Computer and Smartphone, avoid Speaker devices
                if isinstance(device_id, str) and not is_restricted:
                    # Prefer Computer and Smartphone devices
                    is_preferred = device_type in ("Computer", "Smartphone")
                    device_tuple = (device_id, device, is_preferred)

                    if is_active:
                        active_controllable_devices.append(device_tuple)
                    else:
                        controllable_devices.append(device_tuple)

            # Sort by preference (preferred devices first) within each group
            def sort_key(item: tuple[str, dict[str, Any], bool]) -> tuple[bool, str]:
                device_id, _, is_preferred = item
                return (not is_preferred, device_id)  # False (preferred) comes before True

            active_controllable_devices.sort(key=sort_key)
            controllable_devices.sort(key=sort_key)

            # Try active devices first (if any)
            device_list = active_controllable_devices + controllable_devices

            for device_id, _, _ in device_list:
                # Transfer to this controllable device
                try:
                    await self.transfer_playback(user_id, device_id=device_id, play=False)
                    # Give Spotify a moment to complete the transfer
                    await asyncio.sleep(0.5)

                    # Verify the transfer worked
                    player_state = await self.get_player(user_id)
                    if player_state:
                        active_device = player_state.get("device", {})
                        if isinstance(active_device, dict):
                            active_device_id = active_device.get("id")
                            if active_device_id == device_id:
                                # Transfer successful
                                return device_id

                    # If we can't verify, still return the device_id and let it try
                    return device_id
                except SpotifyClientError:
                    # Transfer failed, try next device
                    continue

        except SpotifyClientError:
            # Could not get devices, return None
            pass

        # No controllable devices found
        return None

    async def play(
        self,
        user_id: int,
        *,
        device_id: str | None = None,
        allow_transfer: bool = False,
        uris: Sequence[str] | None = None,
        context_uri: str | None = None,
    ) -> None:
        # If no device_id specified, ensure we're on a controllable device
        if device_id is None:
            device_id = await self._ensure_controllable_device(
                user_id, allow_transfer=allow_transfer
            )
            if device_id is None:
                raise SpotifyClientError(
                    "No controllable device available. Please start Spotify on a "
                    "device that supports Web API control (not Sonos or other "
                    "restricted devices)."
                )

        payload: dict[str, Any] = {}
        if uris:
            payload["uris"] = list(uris)
        if context_uri:
            payload["context_uri"] = context_uri
        params = {"device_id": device_id} if device_id else None
        await self._request(
            user_id,
            "PUT",
            "/me/player/play",
            json=payload or None,
            params=params,
            expected_status=(204,),
        )

    async def pause(
        self, user_id: int, *, device_id: str | None = None, allow_transfer: bool = False
    ) -> None:
        # If no device_id specified, ensure we're on a controllable device
        if device_id is None:
            device_id = await self._ensure_controllable_device(
                user_id, allow_transfer=allow_transfer
            )
            if device_id is None:
                raise SpotifyClientError(
                    "No controllable device available. Please start Spotify on a "
                    "device that supports Web API control (not Sonos or other "
                    "restricted devices)."
                )

        params = {"device_id": device_id} if device_id else None
        await self._request(
            user_id,
            "PUT",
            "/me/player/pause",
            params=params,
            expected_status=(204,),
        )

    async def next_track(
        self, user_id: int, *, device_id: str | None = None, allow_transfer: bool = False
    ) -> None:
        # If no device_id specified, ensure we're on a controllable device
        if device_id is None:
            device_id = await self._ensure_controllable_device(
                user_id, allow_transfer=allow_transfer
            )
            if device_id is None:
                raise SpotifyClientError(
                    "No controllable device available. Please start Spotify on a "
                    "device that supports Web API control (not Sonos or other "
                    "restricted devices)."
                )

        params = {"device_id": device_id} if device_id else None
        await self._request(
            user_id,
            "POST",
            "/me/player/next",
            params=params,
            expected_status=(204,),
        )

    async def previous_track(
        self, user_id: int, *, device_id: str | None = None, allow_transfer: bool = False
    ) -> None:
        # If no device_id specified, ensure we're on a controllable device
        if device_id is None:
            device_id = await self._ensure_controllable_device(
                user_id, allow_transfer=allow_transfer
            )
            if device_id is None:
                raise SpotifyClientError(
                    "No controllable device available. Please start Spotify on a "
                    "device that supports Web API control (not Sonos or other "
                    "restricted devices)."
                )

        params = {"device_id": device_id} if device_id else None
        await self._request(
            user_id,
            "POST",
            "/me/player/previous",
            params=params,
            expected_status=(204,),
        )

    async def search_track(
        self,
        user_id: int,
        *,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        response = await self._request(
            user_id,
            "GET",
            "/search",
            params={"q": query, "type": "track", "limit": limit},
        )
        payload = response.json()
        tracks = payload.get("tracks", {})
        items = tracks.get("items") if isinstance(tracks, dict) else None
        if not isinstance(items, list):
            return []
        return items

    async def create_playlist(
        self,
        user_id: int,
        *,
        name: str,
        description: str,
        public: bool = False,
    ) -> dict[str, Any]:
        profile = await self.get_profile(user_id)
        spotify_user_id = profile.get("id")
        if not isinstance(spotify_user_id, str):
            raise SpotifyClientError("Unable to determine Spotify user id")

        # Sanitize playlist name: remove/replace problematic characters
        # Spotify has undocumented restrictions on certain characters
        sanitized_name = _sanitize_playlist_name(name)

        # Sanitize description as well
        sanitized_description = description[:300] if description else ""

        logger.debug(
            "Creating playlist: name=%r (sanitized=%r), description_len=%d, public=%s",
            name,
            sanitized_name,
            len(sanitized_description),
            public,
        )

        response = await self._request(
            user_id,
            "POST",
            f"/users/{spotify_user_id}/playlists",
            json={
                "name": sanitized_name,
                "description": sanitized_description,
                "public": public,
            },
            expected_status=(201,),
        )
        return response.json()  # type: ignore[no-any-return]

    async def add_tracks(
        self,
        user_id: int,
        *,
        playlist_id: str,
        track_uris: Sequence[str],
    ) -> dict[str, Any]:
        response = await self._request(
            user_id,
            "POST",
            f"/playlists/{playlist_id}/tracks",
            json={"uris": list(track_uris)},
            expected_status=(201,),
        )
        return response.json()  # type: ignore[no-any-return]

    async def get_top_artists(
        self,
        user_id: int,
        *,
        time_range: str = "medium_term",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Get user's top artists.

        Args:
            user_id: Telegram user ID
            time_range: Time range for top artists - "short_term" (4 weeks),
                       "medium_term" (6 months), or "long_term" (several years)
            limit: Number of artists to return (max 50)
        """
        response = await self._request(
            user_id,
            "GET",
            "/me/top/artists",
            params={"time_range": time_range, "limit": min(limit, 50)},
        )
        payload = response.json()
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        return items

    async def get_top_tracks(
        self,
        user_id: int,
        *,
        time_range: str = "medium_term",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Get user's top tracks.

        Args:
            user_id: Telegram user ID
            time_range: Time range for top tracks - "short_term" (4 weeks),
                       "medium_term" (6 months), or "long_term" (several years)
            limit: Number of tracks to return (max 50)
        """
        response = await self._request(
            user_id,
            "GET",
            "/me/top/tracks",
            params={"time_range": time_range, "limit": min(limit, 50)},
        )
        payload = response.json()
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        return items

    async def get_recently_played(
        self,
        user_id: int,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Get user's recently played tracks.

        Args:
            user_id: Telegram user ID
            limit: Number of recently played items to return (max 50)
        """
        response = await self._request(
            user_id,
            "GET",
            "/me/player/recently-played",
            params={"limit": min(limit, 50)},
        )
        payload = response.json()
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        return items


__all__ = [
    "RepositoryTokenStore",
    "SpotifyClient",
    "SpotifyClientError",
]
