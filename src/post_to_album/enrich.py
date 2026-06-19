from __future__ import annotations

from typing import Any

from post_to_album.models import EnrichedPost, SourcePost, Track
from post_to_album.normalize import to_wp_date_yyyymmdd


def build_enriched_post(
    source: SourcePost,
    genre_tags: list[str],
    lastfm_release_id: str | None,
    lastfm_release_date: str | None,
    lastfm_lastfm_album_url: str | None,
    lastfm_confidence: str,
    lastfm_score: int,
) -> EnrichedPost:
    updates: dict[str, Any] = {}
    updates.update(track_stats_update(source.tracks))
    updates.update(tracks_writeback_update(source))
    updates.update(release_date_update(lastfm_release_date))
    updates.update(listen_event_date_update(source))
    updates.update(spotify_album_url_update(source.acf))
    if lastfm_lastfm_album_url:
        updates["lastfm_album_url"] = lastfm_lastfm_album_url
    updates.update(match_metadata_update(lastfm_release_id, lastfm_confidence, lastfm_score))
    updates.update(unreleased_flag_update(source.acf))
    if genre_tags:
        updates["music_mood_tags"] = genre_tags
    taxonomy: dict[str, list[str]] = {"genre": genre_tags} if genre_tags else {}
    return EnrichedPost(source.post_id, updates, taxonomy)


def track_stats_update(tracks: list[Track]) -> dict[str, Any]:
    if not tracks:
        return {}
    positive = [duration for duration in _full_durations(tracks) if duration > 0]
    updates: dict[str, Any] = {"music_total_tracks": len(tracks)}
    if positive:
        total = sum(positive)
        updates["music_length_ms"] = total
        updates["music_avg_track_ms"] = round(total / len(positive))
    if any(track.explicit for track in tracks):
        updates["music_explicit"] = True
    return updates


def tracks_writeback_update(source: SourcePost) -> dict[str, Any]:
    if not source.tracks:
        return {}
    normalized = [track.to_dict() for track in source.tracks]
    if normalized == _rows(source.acf.get("music_tracks")):
        return {}
    return {"music_tracks": normalized}


def release_date_update(lastfm_release_date: str | None) -> dict[str, Any]:
    date = to_wp_date_yyyymmdd(lastfm_release_date)
    if not date:
        return {}
    return {"music_release_date": date}


def listen_event_date_update(source: SourcePost) -> dict[str, Any]:
    date = to_wp_date_yyyymmdd(source.published_at)
    if not date:
        return {}
    return {"music_listened_at": date}


def spotify_album_url_update(acf: dict) -> dict[str, Any]:
    from post_to_album.lastfm import spotify_album_url_from_id

    url = spotify_album_url_from_id(_optional_str(acf.get("spotify_album_id")))
    if not url:
        return {}
    if acf.get("spotify_album_url") == url:
        return {}
    return {"spotify_album_url": url}


def match_metadata_update(
    lastfm_release_id: str | None,
    confidence: str,
    score: int,
) -> dict[str, Any]:
    updates: dict[str, Any] = {"music_match_confidence": confidence}
    if lastfm_release_id:
        updates["lastfm_release_id"] = lastfm_release_id
    return updates


def unreleased_flag_update(acf: dict) -> dict[str, Any]:
    from post_to_album.normalize import parse_bool

    return {"unreleased": parse_bool(acf.get("unreleased"))}


def _full_durations(tracks: list[Track]) -> list[int]:
    return [track.duration_ms or 0 for track in tracks]


def _rows(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
