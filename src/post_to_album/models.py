from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Track:
    disc_number: int | None
    track_number: int | None
    title: str
    duration_ms: int | None
    spotify_id: str | None
    highlight: bool
    explicit: bool


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


@dataclass(frozen=True, slots=True)
class EnrichedPost:
    post_id: int
    acf_updates: dict
    taxonomy_updates: dict[str, list[str]]
