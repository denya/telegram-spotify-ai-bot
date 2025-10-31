"""Spotify authorization routing (stubs for now)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status


router = APIRouter(prefix="/spotify", tags=["spotify"])


@router.get("/start", summary="Begin Spotify authorization")
async def start_authorization() -> None:
    """Placeholder for Spotify authorization start sequence."""

    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Not implemented")


@router.get("/callback", summary="Handle Spotify authorization callback")
async def authorization_callback() -> None:
    """Placeholder for Spotify authorization callback handler."""

    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Not implemented")


__all__ = ["router", "start_authorization", "authorization_callback"]
