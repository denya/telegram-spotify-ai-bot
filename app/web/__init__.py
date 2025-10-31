"""Web surface for Spotify auth and health endpoints."""

from .app import create_web_app
from . import auth_routes, health

__all__ = ["create_web_app", "auth_routes", "health"]
