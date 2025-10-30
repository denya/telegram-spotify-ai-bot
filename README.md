## Telegram Spotify AI Bot

Lean Telegram bot that connects personal Spotify accounts, exposes playback controls, and orchestrates Claude-powered thematic playlists with web search context.

### Core Capabilities
- Env-configurable Spotify OAuth (Authorization Code with PKCE)
- Telegram onboarding with inline keyboards
- Playback snapshot and play/pause controls
- Playlist browsing with "Load more"
- Claude + web search assisted playlist creation (Phase 2)

### Tech Stack
- Python 3.14
- aiogram 3 (Telegram bot)
- FastAPI + Uvicorn (auth callback service)
- SQLite (minimal persistence)
- httpx (Spotify API client)
- Anthropic Claude with web search

### Local Setup
1. Create a virtual environment using Python 3.14.
2. Copy `.env.example` to `.env` and fill in the tokens and secrets.
3. Install dependencies with `pip install -r requirements.txt` (file to be added in later tasks).
4. Run the bot entry point (TBD) after completing the Spotify authorization steps.

Implementation is staged per `docs/roadmap.md` (to be added) and tracked via project to-dos.

