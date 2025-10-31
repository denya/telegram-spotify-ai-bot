"""Database schema helpers for the Telegram Spotify AI Bot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import aiosqlite

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL UNIQUE,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS auth_states (
        state TEXT PRIMARY KEY,
        user_id INTEGER,
        code_verifier TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS spotify_tokens (
        user_id INTEGER PRIMARY KEY,
        access_token TEXT NOT NULL,
        refresh_token TEXT,
        scope TEXT NOT NULL,
        token_type TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS mix_rate_limits (
        user_id INTEGER NOT NULL,
        request_date TEXT NOT NULL,
        request_count INTEGER NOT NULL DEFAULT 0,
        last_request_at INTEGER,
        processing_until INTEGER,
        PRIMARY KEY (user_id, request_date),
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    """.strip(),
)


@dataclass(slots=True, frozen=True)
class SchemaStats:
    """Execution statistics for schema migrations."""

    statements_executed: int


async def apply_schema(connection: aiosqlite.Connection) -> SchemaStats:
    """Apply idempotent schema statements to the connected SQLite database."""

    count = 0
    for statement in SCHEMA_STATEMENTS:
        await connection.execute(statement)
        count += 1
    return SchemaStats(statements_executed=count)


async def ensure_schema(db_path: Path) -> SchemaStats:
    """Open a connection, apply the schema, and close the connection."""

    async with aiosqlite.connect(str(db_path)) as connection:
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON;")
        stats = await apply_schema(connection)
        await connection.commit()
    return stats


__all__ = ["SCHEMA_STATEMENTS", "SchemaStats", "apply_schema", "ensure_schema"]
