## Telegram Spotify AI Bot

Minimal Telegram assistant that wires up Spotify playback control and Claude-powered playlist generation.

### Core Capabilities
- Spotify OAuth (Authorization Code + PKCE) with SQLite token storage
- Telegram `/start` onboarding with inline keyboards for playback control
- Playback snapshot via `/now` plus inline play/pause/next/previous buttons
- `/mix <context>` command that asks Anthropic Claude for 25 tracks, creates a Spotify playlist, and shares the link

### Tech Stack
- Python 3.13
- aiogram 3 for the Telegram bot
- FastAPI (auth callback) served by Uvicorn
- SQLite via `aiosqlite`
- httpx (Spotify Web API)
- Anthropic Claude for playlist planning

### Prerequisites
1. Python 3.13 and virtual environment tooling
2. Spotify developer app with redirect URI `http://localhost:8000/spotify/callback`
3. Telegram bot token from [BotFather](https://core.telegram.org/bots#botfather)
4. Anthropic API key (for playlist generation)

### Local Setup
1. `python -m venv .venv && source .venv/bin/activate`
2. `cp env.example .env` and fill in required values
3. `pip install -r requirements.txt -r requirements-dev.txt` or `make install`
4. In terminal A: `make run-web` (FastAPI + OAuth callback on port 8000)
5. In terminal B: `make run-bot` (Telegram polling bot)
6. Talk to your Telegram bot `/start`, follow the login link, then try `/now` or `/mix chill vitamin d`

### Useful Commands
- `make fmt` – format + lint fixes
- `make lint` – static analysis (`ruff`, `mypy`)
- `make test` – run pytest suite
- `make dev` – run web callback and bot concurrently (Ctrl+C to stop)

Project tasks are tracked in the repo TODOs.

