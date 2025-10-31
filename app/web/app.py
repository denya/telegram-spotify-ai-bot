"""Factory for the FastAPI web application."""

from __future__ import annotations

from fastapi import FastAPI

from . import auth_routes, health


def create_web_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""

    app = FastAPI(title="Telegram Spotify Web API", version="0.1.0")
    app.include_router(health.router)
    app.include_router(auth_routes.router)
    return app


__all__ = ["create_web_app"]
