"""Application entry point for the Telegram Spotify AI Bot."""

from __future__ import annotations

from fastapi import FastAPI

from .web import create_web_app


def create_app() -> FastAPI:
    """Expose a FastAPI application for ASGI servers."""

    return create_web_app()


app = create_app()


def run() -> None:  # pragma: no cover - convenience wrapper
    """Run the FastAPI application using uvicorn."""

    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False, factory=False)


if __name__ == "__main__":  # pragma: no cover
    run()
