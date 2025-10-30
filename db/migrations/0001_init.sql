-- Create core tables for Telegram Spotify AI Bot

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL UNIQUE,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS spotify_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    spotify_user_id TEXT NOT NULL,
    display_name TEXT,
    scopes TEXT NOT NULL,
    access_token_enc TEXT NOT NULL,
    refresh_token_enc TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_spotify_accounts_user
    ON spotify_accounts (user_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_spotify_accounts_external
    ON spotify_accounts (spotify_user_id);

CREATE TABLE IF NOT EXISTS auth_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    state TEXT NOT NULL UNIQUE,
    code_verifier TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    used_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_auth_states_user ON auth_states (user_id);
