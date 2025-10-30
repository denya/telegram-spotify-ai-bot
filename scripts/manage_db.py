"""Utility script to apply SQLite schema migrations."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Iterable

ROOT_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT_DIR / "db" / "migrations"
DEFAULT_DB_PATH = ROOT_DIR / "data" / "app.db"


class MigrationError(RuntimeError):
    """Raised when migration application fails."""


def iter_sql_files(directory: Path) -> Iterable[Path]:
    for path in sorted(directory.glob("*.sql")):
        if path.is_file():
            yield path


def apply_migrations(database: Path, migrations_dir: Path = MIGRATIONS_DIR) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        for sql_file in iter_sql_files(migrations_dir):
            conn.executescript(sql_file.read_text(encoding="utf-8"))
        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply SQLite migrations")
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to SQLite database file (default: data/app.db)",
    )
    parser.add_argument(
        "--migrations",
        type=Path,
        default=MIGRATIONS_DIR,
        help="Directory containing .sql migration files",
    )
    args = parser.parse_args()

    try:
        apply_migrations(args.database, args.migrations)
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        raise MigrationError("Failed to apply migrations") from exc


if __name__ == "__main__":  # pragma: no cover
    main()
