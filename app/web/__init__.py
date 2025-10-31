"""Web surface for Spotify auth and health endpoints."""

from . import auth_routes, health
from .app import create_web_app

__all__ = ["auth_routes", "create_web_app", "health"]
