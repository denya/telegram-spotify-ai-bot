"""Tests covering auth state repository helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import repository, schema


@pytest.mark.asyncio
async def test_auth_state_crud(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    await schema.ensure_schema(db_path)

    profile = repository.UserProfile(telegram_id=12345, username="tester")

    async with repository.connect(db_path) as connection:
        user_id = await repository.ensure_user(connection, profile)
        await repository.insert_auth_state(
            connection, state="state-token", code_verifier="code123", user_id=user_id
        )
        await connection.commit()

    async with repository.connect(db_path) as connection:
        state = await repository.fetch_auth_state(connection, "state-token")
        assert state is not None
        assert state.code_verifier == "code123"
        assert state.user_id == user_id

        await repository.delete_auth_state(connection, "state-token")
        await connection.commit()

    async with repository.connect(db_path) as connection:
        assert await repository.fetch_auth_state(connection, "state-token") is None
