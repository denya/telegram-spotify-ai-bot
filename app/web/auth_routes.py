"""Spotify authorization routing (start + callback endpoints)."""

from __future__ import annotations

import logging
from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import SPOTIFY_SCOPES, Settings, load_settings
from ..db import repository, schema
from ..spotify import auth as spotify_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/spotify", tags=["spotify"])


def _get_settings() -> Settings:
    return load_settings()


SettingsDep = Annotated[Settings, Depends(_get_settings)]


@router.get(
    "/login",
    summary="Begin Spotify authorization",
    status_code=status.HTTP_303_SEE_OTHER,
)
async def start_authorization(
    *,
    telegram_id: int = Query(..., description="Numeric Telegram user identifier"),
    username: str | None = Query(None, description="Telegram username"),
    first_name: str | None = Query(None, description="Telegram first name"),
    last_name: str | None = Query(None, description="Telegram last name"),
    settings: SettingsDep,
) -> RedirectResponse:
    """Kick off the Spotify Authorization Code + PKCE flow."""

    profile = repository.UserProfile(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
    )
    code_verifier = spotify_auth.generate_code_verifier()
    code_challenge = spotify_auth.generate_code_challenge(code_verifier)

    for attempt in range(5):
        state = spotify_auth.generate_state()
        try:
            async with repository.connect(settings.db_path) as connection:
                await schema.apply_schema(connection)
                user_id = await repository.ensure_user(connection, profile)
                await repository.insert_auth_state(
                    connection, state=state, code_verifier=code_verifier, user_id=user_id
                )
                await connection.commit()
            break
        except aiosqlite.IntegrityError:  # pragma: no cover - extremely unlikely
            logger.debug("State collision detected, regenerating (attempt %s)", attempt + 1)
    else:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail="Unable to allocate authorization state")

    redirect_url = spotify_auth.build_authorization_url(
        client_id=settings.spotify_client_id,
        redirect_uri=settings.spotify_redirect_uri,
        state=state,
        code_challenge=code_challenge,
        scopes=SPOTIFY_SCOPES,
        show_dialog=False,
    )

    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/callback", summary="Handle Spotify authorization callback")
async def authorization_callback(
    *,
    code: str | None = Query(None, description="Authorization code from Spotify"),
    state: str | None = Query(None, description="CSRF prevention state token"),
    error: str | None = Query(None, description="Spotify error returned during auth"),
    settings: SettingsDep,
) -> HTMLResponse:
    """Exchange the authorization code and persist Spotify credentials."""

    if error is not None:
        logger.warning("Spotify authorization failed: %s", error)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    if code is None or state is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code or state")

    async with repository.connect(settings.db_path) as connection:
        await schema.apply_schema(connection)
        auth_state = await repository.fetch_auth_state(connection, state)
        if auth_state is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Authorization session not found or expired",
            )
        if auth_state.user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="State missing user binding"
            )

        try:
            token_response = await spotify_auth.exchange_code_for_tokens(
                client_id=settings.spotify_client_id,
                client_secret=settings.spotify_client_secret,
                code=code,
                redirect_uri=settings.spotify_redirect_uri,
                code_verifier=auth_state.code_verifier,
            )
        except spotify_auth.SpotifyAuthError as exc:
            logger.exception("Failed to exchange Spotify authorization code for state %s", state)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unable to exchange authorization code with Spotify",
            ) from exc

        expires_at = spotify_auth.compute_expiry(token_response.expires_in)
        await repository.upsert_spotify_tokens(
            connection,
            user_id=auth_state.user_id,
            access_token=token_response.access_token,
            refresh_token=token_response.refresh_token,
            scope=token_response.scope,
            token_type=token_response.token_type,
            expires_at=expires_at,
        )
        await repository.delete_auth_state(connection, state)
        await connection.commit()

    html = (
        "<html><body><h1>All set!</h1><p>You can close this tab and return to Telegram.</p>"
        "</body></html>"
    )
    return HTMLResponse(content=html)


__all__ = ["authorization_callback", "router", "start_authorization"]
