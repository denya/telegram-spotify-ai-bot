"""Async Spotify Web API client with token refresh support."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from ..db import repository
from . import auth as spotify_auth


class SpotifyClientError(RuntimeError):
    """Raised when Spotify API responses indicate an error."""


@dataclass(slots=True)
class RepositoryTokenStore:
    """Simple wrapper around sqlite-backed token persistence."""

    db_path: Path

    async def load(self, user_id: int) -> repository.SpotifyTokens | None:
        async with repository.connect(self.db_path) as connection:
            return await repository.get_spotify_tokens(connection, user_id)

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
            tokens = await self._token_store.load(user_id)
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
        response = await spotify_auth.refresh_access_token(
            client_id=self._client_id,
            client_secret=self._client_secret,
            refresh_token=refresh_token,
        )
        scope = response.scope or tokens.scope
        token_type = response.token_type or tokens.token_type
        persisted_refresh = response.refresh_token or refresh_token
        expires_at = spotify_auth.compute_expiry(response.expires_in)
        updated = await self._token_store.save(
            user_id,
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
        tokens = await self._ensure_fresh_tokens(user_id)
        headers = kwargs.pop("headers", {})
        headers.setdefault("Authorization", f"Bearer {tokens.access_token}")
        response = await self._http.request(method, path, headers=headers, **kwargs)

        if response.status_code == 401 and retry:
            tokens = await self._refresh_tokens(user_id, tokens)
            headers["Authorization"] = f"Bearer {tokens.access_token}"
            response = await self._http.request(method, path, headers=headers, **kwargs)

        ok_status = expected_status or (200, 201, 202, 204)
        if response.status_code not in ok_status:
            raise SpotifyClientError(
                f"Spotify request failed ({response.status_code}): {response.text}"
            )
        return response

    async def get_profile(self, user_id: int) -> dict[str, Any]:
        response = await self._request(user_id, "GET", "/me")
        return response.json()

    async def get_currently_playing(self, user_id: int) -> dict[str, Any] | None:
        response = await self._request(
            user_id,
            "GET",
            "/me/player/currently-playing",
            expected_status=(200, 204),
        )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def play(
        self,
        user_id: int,
        *,
        device_id: str | None = None,
        uris: Sequence[str] | None = None,
        context_uri: str | None = None,
    ) -> None:
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

    async def pause(self, user_id: int, *, device_id: str | None = None) -> None:
        params = {"device_id": device_id} if device_id else None
        await self._request(
            user_id,
            "PUT",
            "/me/player/pause",
            params=params,
            expected_status=(204,),
        )

    async def next_track(self, user_id: int, *, device_id: str | None = None) -> None:
        params = {"device_id": device_id} if device_id else None
        await self._request(
            user_id,
            "POST",
            "/me/player/next",
            params=params,
            expected_status=(204,),
        )

    async def previous_track(self, user_id: int, *, device_id: str | None = None) -> None:
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
        response = await self._request(
            user_id,
            "POST",
            f"/users/{spotify_user_id}/playlists",
            json={"name": name, "description": description, "public": public},
            expected_status=(201,),
        )
        return response.json()

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
        return response.json()


__all__ = [
    "RepositoryTokenStore",
    "SpotifyClient",
    "SpotifyClientError",
]
