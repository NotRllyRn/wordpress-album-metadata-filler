from __future__ import annotations

from dataclasses import dataclass

from post_to_album.models import EnrichedPost, SourcePost
from post_to_album.normalize import derive_avg_track_ms, derive_total_length_ms, derive_total_tracks, parse_bool


@dataclass(frozen=True, slots=True)
class DiffResult:
    changed: bool
    acf_updates: dict
    taxonomy_updates: dict[str, list[str]]
    reasons: list[str]


def build_enriched_post(
    source: SourcePost,
    genre_tags: list[str],
    confidence: str,
    score: int,
) -> EnrichedPost:
    acf_updates = {
        "music_total_tracks": derive_total_tracks(source.tracks),
        "music_length_ms": derive_total_length_ms(source.tracks),
        "music_avg_track_ms": derive_avg_track_ms(source.tracks),
        "music_match_confidence": confidence,
        "unreleased": parse_bool(source.acf.get("unreleased")),
    }
    if genre_tags:
        acf_updates["music_mood_tags"] = genre_tags
    return EnrichedPost(source.post_id, acf_updates, {"genre": genre_tags} if genre_tags else {})


def diff_post(source: SourcePost, enriched: EnrichedPost) -> DiffResult:
    acf_updates = {
        key: value
        for key, value in enriched.acf_updates.items()
        if source.acf.get(key) != value
    }
    taxonomy_updates = {
        key: value
        for key, value in enriched.taxonomy_updates.items()
        if value
    }
    changed = bool(acf_updates or taxonomy_updates)
    return DiffResult(
        changed=changed,
        acf_updates=acf_updates,
        taxonomy_updates=taxonomy_updates,
        reasons=["field-diff"] if changed else ["already-normalized"],
    )
