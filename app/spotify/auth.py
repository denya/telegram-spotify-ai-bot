"""Spotify authorization utilities (PKCE helpers, scope handling)."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from ..config import SPOTIFY_SCOPES

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"  # noqa: S105 - OAuth endpoint
DEFAULT_SCOPES = SPOTIFY_SCOPES
DEFAULT_TIMEOUT = httpx.Timeout(10.0, read=10.0)


class SpotifyAuthError(RuntimeError):
    """Raised when an OAuth exchange with Spotify fails."""


class RevokedTokenError(SpotifyAuthError):
    """Raised when a refresh token has been revoked and re-authentication is required."""


@dataclass(slots=True)
class TokenResponse:
    """Normalized token payload returned by Spotify."""

    access_token: str
    expires_in: int
    scope: str
    token_type: str
    refresh_token: str | None


def generate_code_verifier(length: int = 96) -> str:
    """Return a securely generated PKCE code verifier string."""

    # token_urlsafe returns approximately 4/3 bytes, ensuring required length range (43-128)
    verifier = secrets.token_urlsafe(length)
    return verifier[:128]


def generate_code_challenge(code_verifier: str) -> str:
    """Derive an S256 code challenge from a verifier."""

    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")


def generate_state() -> str:
    """Return a random state string to prevent CSRF in OAuth flow."""

    return secrets.token_urlsafe(32)


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scopes: Iterable[str] = DEFAULT_SCOPES,
    show_dialog: bool = False,
) -> str:
    """Compose the Spotify authorization URL with PKCE parameters."""

    scope_value = " ".join(scopes)
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": scope_value,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "show_dialog": str(show_dialog).lower(),
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _build_auth_header(client_id: str, client_secret: str | None) -> tuple[str, str] | None:
    if client_secret is None:
        return None
    credentials = f"{client_id}:{client_secret}".encode()
    encoded = base64.b64encode(credentials).decode()
    return "Authorization", f"Basic {encoded}"


async def _post_token_request(
    data: dict[str, str],
    *,
    client_id: str,
    client_secret: str | None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, object]:
    owns_client = http_client is None
    headers: dict[str, str] = {"Content-Type": "application/x-www-form-urlencoded"}
    auth_header = _build_auth_header(client_id, client_secret)
    if auth_header is not None:
        key, value = auth_header
        headers[key] = value
    else:
        data.setdefault("client_id", client_id)

    http = http_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        response = await http.post(TOKEN_URL, data=data, headers=headers)
        if response.status_code >= 400:
            # Check if this is a revoked token error
            try:
                error_data = response.json()
                if isinstance(error_data, dict):
                    error = error_data.get("error")
                    error_description = error_data.get("error_description", "")
                    if error == "invalid_grant" and "revoked" in error_description.lower():
                        raise RevokedTokenError("Refresh token has been revoked")
            except (ValueError, TypeError, AttributeError):
                # Failed to parse JSON or check error, fall through to generic error
                pass
            raise SpotifyAuthError(
                f"Spotify token endpoint returned {response.status_code}: {response.text}"
            )
        payload = response.json()
        if not isinstance(payload, dict):  # pragma: no cover - defensive
            raise SpotifyAuthError("Unexpected response from Spotify token endpoint")
        return payload
    except httpx.HTTPError as exc:
        raise SpotifyAuthError("Failed to contact Spotify token endpoint") from exc
    finally:
        if owns_client:
            await http.aclose()


def _parse_token_payload(payload: dict[str, object]) -> TokenResponse:
    try:
        access_token = str(payload["access_token"])
        token_type = str(payload.get("token_type", "Bearer"))
        scope = str(payload.get("scope", ""))
        expires_in = int(payload.get("expires_in", 0))
        refresh_raw = payload.get("refresh_token")
        refresh_token = str(refresh_raw) if refresh_raw is not None else None
    except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise SpotifyAuthError("Invalid token payload from Spotify") from exc
    return TokenResponse(
        access_token=access_token,
        expires_in=expires_in,
        scope=scope,
        token_type=token_type,
        refresh_token=refresh_token,
    )


async def exchange_code_for_tokens(
    *,
    client_id: str,
    client_secret: str | None,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    http_client: httpx.AsyncClient | None = None,
) -> TokenResponse:
    """Exchange an authorization code for Spotify access and refresh tokens."""

    payload = await _post_token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        client_id=client_id,
        client_secret=client_secret,
        http_client=http_client,
    )
    return _parse_token_payload(payload)


async def refresh_access_token(
    *,
    client_id: str,
    client_secret: str | None,
    refresh_token: str,
    http_client: httpx.AsyncClient | None = None,
) -> TokenResponse:
    """Refresh a Spotify access token using the long-lived refresh token."""

    payload = await _post_token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        client_id=client_id,
        client_secret=client_secret,
        http_client=http_client,
    )
    if "refresh_token" not in payload:
        payload["refresh_token"] = refresh_token
    return _parse_token_payload(payload)


def compute_expiry(expires_in: int, *, now: datetime | None = None) -> datetime:
    """Convert an expires_in duration to an absolute UTC timestamp."""

    base = now or datetime.now(tz=UTC)
    if base.tzinfo is None:  # pragma: no cover - defensive
        base = base.replace(tzinfo=UTC)
    return base + timedelta(seconds=expires_in)


def should_refresh(expires_at: datetime, *, skew_seconds: int = 90) -> bool:
    """Return True when the token needs a refresh, allowing for clock skew."""

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    threshold = datetime.now(tz=UTC) + timedelta(seconds=skew_seconds)
    return expires_at <= threshold


__all__ = [
    "AUTHORIZE_URL",
    "DEFAULT_SCOPES",
    "TOKEN_URL",
    "RevokedTokenError",
    "SpotifyAuthError",
    "TokenResponse",
    "build_authorization_url",
    "compute_expiry",
    "exchange_code_for_tokens",
    "generate_code_challenge",
    "generate_code_verifier",
    "generate_state",
    "refresh_access_token",
    "should_refresh",
]
