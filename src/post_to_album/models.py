from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Track:
    disc_number: int | None
    track_number: int | None
    title: str
    duration_ms: int | None
    spotify_id: str | None
    highlight: bool
    explicit: bool

    def to_dict(self) -> dict:
        return {
            "disc_number": self.disc_number,
            "track_number": self.track_number,
            "title": self.title,
            "duration_ms": self.duration_ms,
            "spotify_id": self.spotify_id,
            "highlight": self.highlight,
            "explicit": self.explicit,
        }


@dataclass(frozen=True, slots=True)
class TrackSummary:
    total_tracks: int
    total_duration_ms: int | None
    avg_track_ms: int | None
    explicit: bool


@dataclass(frozen=True, slots=True)
class PreviousListen:
    listen_order: int
    post_id: int


@dataclass(frozen=True, slots=True)
class SourcePost:
    post_id: int
    title: str
    acf: dict
    tracks: list[Track]
    previous_listens: list[PreviousListen]
    published_at: str | None


@dataclass(frozen=True, slots=True)
class EnrichedPost:
    post_id: int
    acf_updates: dict[str, Any]
    taxonomy_updates: dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class LastfmMatch:
    release_id: str | None
    release_date: str | None
    tags: list[str]
    confidence: str
    score: int
