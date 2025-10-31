"""Unit tests for the playlist planner plain text parsing helpers."""

from __future__ import annotations

import pytest

from app.ai.playlist_planner import MAX_TRACKS, PlannedTrack, PlaylistPlannerError, _parse_tracks


def _build_payload(count: int = MAX_TRACKS) -> str:
    """Build plain text payload in 'artist - song' format."""
    lines = [f"Artist {index} - Song {index}" for index in range(count)]
    return "\n".join(lines)


def test_parse_tracks_success() -> None:
    raw = _build_payload()
    plan = _parse_tracks(raw)

    assert len(plan.tracks) == MAX_TRACKS
    assert plan.tracks[0] == PlannedTrack(title="Song 0", artist="Artist 0")


def test_parse_tracks_rejects_short_responses() -> None:
    raw = _build_payload(count=MAX_TRACKS - 1)

    with pytest.raises(PlaylistPlannerError):
        _parse_tracks(raw)


def test_parse_tracks_rejects_invalid_format() -> None:
    """Test that invalid plain text format raises an error."""
    with pytest.raises(PlaylistPlannerError):
        _parse_tracks("not a valid track format")
