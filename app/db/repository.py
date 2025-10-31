"""Asynchronous database helpers for SQLite interactions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite


@dataclass(slots=True)
class UserProfile:
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


@dataclass(slots=True)
class AuthState:
    state: str
    code_verifier: str
    user_id: int | None


@dataclass(slots=True)
class SpotifyTokens:
    user_id: int
    access_token: str
    refresh_token: str | None
    scope: str
    token_type: str
    expires_at: datetime


@dataclass(slots=True)
class MixRateLimitResult:
    allowed: bool
    reason: str | None
    request_date: str


@asynccontextmanager
async def connect(db_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """Yield an aiosqlite connection with foreign keys enforced."""

    connection = await aiosqlite.connect(str(db_path))
    connection.row_factory = aiosqlite.Row
    await connection.execute("PRAGMA foreign_keys = ON;")
    try:
        yield connection
    finally:
        await connection.close()


async def get_user_id_by_telegram_id(
    connection: aiosqlite.Connection, telegram_id: int
) -> int | None:
    """Return the internal user id for a given Telegram user id, if it exists."""

    cursor = await connection.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = await cursor.fetchone()
    await cursor.close()
    return int(row["id"]) if row is not None else None


async def get_telegram_id_by_user_id(
    connection: aiosqlite.Connection, user_id: int
) -> int | None:
    """Return the Telegram user id for a given internal user id, if it exists."""

    cursor = await connection.execute("SELECT telegram_id FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    await cursor.close()
    return int(row["telegram_id"]) if row is not None else None


async def ensure_user(connection: aiosqlite.Connection, profile: UserProfile) -> int:
    """Return an internal user id for the provided Telegram profile."""

    cursor = await connection.execute(
        "SELECT id FROM users WHERE telegram_id = ?", (profile.telegram_id,)
    )
    row = await cursor.fetchone()
    await cursor.close()

    if row is not None:
        await connection.execute(
            """
            UPDATE users
               SET username = ?, first_name = ?, last_name = ?, updated_at = datetime('now')
             WHERE id = ?
            """,
            (profile.username, profile.first_name, profile.last_name, row["id"]),
        )
        return int(row["id"])

    cursor = await connection.execute(
        """
        INSERT INTO users (telegram_id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
        """,
        (profile.telegram_id, profile.username, profile.first_name, profile.last_name),
    )
    last_row_id = cursor.lastrowid
    await cursor.close()
    return int(last_row_id)


async def insert_auth_state(
    connection: aiosqlite.Connection,
    *,
    state: str,
    code_verifier: str,
    user_id: int | None,
) -> None:
    """Persist a Spotify auth state/code verifier pair."""

    await connection.execute(
        """
        INSERT INTO auth_states (state, user_id, code_verifier)
        VALUES (?, ?, ?)
        """,
        (state, user_id, code_verifier),
    )


async def fetch_auth_state(connection: aiosqlite.Connection, state: str) -> AuthState | None:
    """Return the stored code verifier for the provided Spotify state, if any."""

    cursor = await connection.execute(
        """
        SELECT state, code_verifier, user_id
          FROM auth_states
         WHERE state = ?
        """,
        (state,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        return None
    user_id = row["user_id"]
    return AuthState(
        state=row["state"],
        code_verifier=row["code_verifier"],
        user_id=int(user_id) if user_id is not None else None,
    )


async def delete_auth_state(connection: aiosqlite.Connection, state: str) -> None:
    """Remove an auth state once it has been consumed."""

    await connection.execute("DELETE FROM auth_states WHERE state = ?", (state,))


def _row_to_tokens(row: aiosqlite.Row) -> SpotifyTokens:
    expires_at = datetime.fromtimestamp(row["expires_at"], tz=UTC)
    return SpotifyTokens(
        user_id=row["user_id"],
        access_token=row["access_token"],
        refresh_token=row["refresh_token"],
        scope=row["scope"],
        token_type=row["token_type"],
        expires_at=expires_at,
    )


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def check_mix_rate_limit(
    connection: aiosqlite.Connection,
    *,
    user_id: int,
    now: datetime,
    daily_limit: int = 20,
    cooldown_seconds: int = 30,
    processing_ttl_seconds: int = 60,
) -> MixRateLimitResult:
    """Check rate limits for the /mix command."""

    normalized_now = _normalize_datetime(now)
    current_ts = int(normalized_now.timestamp())
    request_date = normalized_now.date().isoformat()

    await connection.execute(
        """
        INSERT INTO mix_rate_limits (user_id, request_date)
        VALUES (?, ?)
        ON CONFLICT(user_id, request_date) DO NOTHING
        """,
        (user_id, request_date),
    )

    cursor = await connection.execute(
        """
        SELECT request_count, last_request_at, processing_until
          FROM mix_rate_limits
         WHERE user_id = ? AND request_date = ?
        """,
        (user_id, request_date),
    )
    row = await cursor.fetchone()
    await cursor.close()

    if row is None:
        # Row should always exist thanks to INSERT DO NOTHING above.
        return MixRateLimitResult(True, None, request_date)

    processing_until = row["processing_until"]
    if processing_until is not None and processing_until <= current_ts:
        await connection.execute(
            """
            UPDATE mix_rate_limits
               SET processing_until = NULL
             WHERE user_id = ? AND request_date = ?
            """,
            (user_id, request_date),
        )
        processing_until = None

    if processing_until is not None and processing_until > current_ts:
        remaining = processing_until - current_ts
        reason = "We're working on the previous one, bro. Give me a minute."
        if remaining > 0:
            reason = f"We're working on the previous one, bro. Give me about {remaining}s."
        return MixRateLimitResult(False, reason, request_date)

    request_count = row["request_count"] or 0
    if request_count >= daily_limit:
        return MixRateLimitResult(
            False,
            "That's enough vibes for today, bro. Max 20 mixes per day.",
            request_date,
        )

    last_request_at = row["last_request_at"]
    if last_request_at is not None and current_ts - last_request_at < cooldown_seconds:
        wait_seconds = cooldown_seconds - (current_ts - last_request_at)
        wait_seconds = max(wait_seconds, 1)
        return MixRateLimitResult(
            False,
            f"Slow down bro. Wait {wait_seconds}s before the next mix.",
            request_date,
        )

    return MixRateLimitResult(True, None, request_date)


async def increment_mix_request(
    connection: aiosqlite.Connection,
    *,
    user_id: int,
    request_date: str,
    now: datetime,
) -> None:
    """Increment daily counter and refresh last request timestamp."""

    normalized_now = _normalize_datetime(now)
    current_ts = int(normalized_now.timestamp())
    await connection.execute(
        """
        UPDATE mix_rate_limits
           SET request_count = request_count + 1,
               last_request_at = ?
         WHERE user_id = ? AND request_date = ?
        """,
        (current_ts, user_id, request_date),
    )


async def mark_mix_processing(
    connection: aiosqlite.Connection,
    *,
    user_id: int,
    request_date: str,
    now: datetime,
    ttl_seconds: int = 60,
) -> int:
    """Mark a user as actively processing a /mix request."""

    normalized_now = _normalize_datetime(now)
    expires_at = int(normalized_now.timestamp()) + ttl_seconds
    await connection.execute(
        """
        UPDATE mix_rate_limits
           SET processing_until = ?
         WHERE user_id = ? AND request_date = ?
        """,
        (expires_at, user_id, request_date),
    )
    return expires_at


async def clear_mix_processing(
    connection: aiosqlite.Connection,
    *,
    user_id: int,
    request_date: str,
) -> None:
    """Clear the processing flag for a user."""

    await connection.execute(
        """
        UPDATE mix_rate_limits
           SET processing_until = NULL
         WHERE user_id = ? AND request_date = ?
        """,
        (user_id, request_date),
    )


async def upsert_spotify_tokens(
    connection: aiosqlite.Connection,
    *,
    user_id: int,
    access_token: str,
    refresh_token: str | None,
    scope: str,
    token_type: str,
    expires_at: datetime,
) -> None:
    """Insert or update Spotify tokens for a given user."""

    expires_epoch = int(_normalize_datetime(expires_at).timestamp())
    await connection.execute(
        """
        INSERT INTO spotify_tokens (
            user_id,
            access_token,
            refresh_token,
            scope,
            token_type,
            expires_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            scope = excluded.scope,
            token_type = excluded.token_type,
            expires_at = excluded.expires_at,
            updated_at = datetime('now')
        """,
        (user_id, access_token, refresh_token, scope, token_type, expires_epoch),
    )


async def get_spotify_tokens(
    connection: aiosqlite.Connection, user_id: int
) -> SpotifyTokens | None:
    """Return Spotify tokens for a user, if stored."""

    cursor = await connection.execute(
        """
        SELECT user_id, access_token, refresh_token, scope, token_type, expires_at
          FROM spotify_tokens
         WHERE user_id = ?
        """,
        (user_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        return None
    return _row_to_tokens(row)


async def update_access_token(
    connection: aiosqlite.Connection,
    *,
    user_id: int,
    access_token: str,
    expires_at: datetime,
    scope: str | None = None,
    token_type: str | None = None,
) -> None:
    """Refresh the short-lived access token while leaving refresh token intact."""

    expires_epoch = int(_normalize_datetime(expires_at).timestamp())
    await connection.execute(
        """
        UPDATE spotify_tokens
           SET access_token = ?,
               expires_at = ?,
               scope = COALESCE(?, scope),
               token_type = COALESCE(?, token_type),
               updated_at = datetime('now')
         WHERE user_id = ?
        """,
        (access_token, expires_epoch, scope, token_type, user_id),
    )


async def delete_spotify_tokens(connection: aiosqlite.Connection, user_id: int) -> None:
    """Remove stored Spotify credentials for a user."""

    await connection.execute("DELETE FROM spotify_tokens WHERE user_id = ?", (user_id,))


__all__ = [
    "AuthState",
    "MixRateLimitResult",
    "SpotifyTokens",
    "UserProfile",
    "check_mix_rate_limit",
    "clear_mix_processing",
    "connect",
    "delete_auth_state",
    "delete_spotify_tokens",
    "ensure_user",
    "fetch_auth_state",
    "get_spotify_tokens",
    "get_telegram_id_by_user_id",
    "get_user_id_by_telegram_id",
    "increment_mix_request",
    "insert_auth_state",
    "mark_mix_processing",
    "update_access_token",
    "upsert_spotify_tokens",
]
