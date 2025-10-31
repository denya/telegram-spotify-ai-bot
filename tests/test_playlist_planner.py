"""Unit tests for the playlist planner JSON parsing helpers."""

from __future__ import annotations

import json

import pytest

from app.ai.playlist_planner import MAX_TRACKS, PlannedTrack, PlaylistPlannerError, _parse_tracks


def _build_payload(count: int = MAX_TRACKS) -> str:
    data = {
        "tracks": [
            {
                "title": f"Song {index}",
                "artist": f"Artist {index}",
            }
            for index in range(count)
        ]
    }
    return json.dumps(data)


def test_parse_tracks_success() -> None:
    raw = _build_payload()
    plan = _parse_tracks(raw)

    assert len(plan.tracks) == MAX_TRACKS
    assert plan.tracks[0] == PlannedTrack(title="Song 0", artist="Artist 0")


def test_parse_tracks_rejects_short_responses() -> None:
    raw = _build_payload(count=MAX_TRACKS - 1)

    with pytest.raises(PlaylistPlannerError):
        _parse_tracks(raw)


def test_parse_tracks_rejects_invalid_json() -> None:
    with pytest.raises(PlaylistPlannerError):
        _parse_tracks("not-json")
