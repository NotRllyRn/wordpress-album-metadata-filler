from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from post_to_album.models import PreviousListen, SourcePost, Track, TrackSummary


def normalize_source_post(raw: Mapping[str, Any]) -> SourcePost:
    acf = raw.get("acf") if isinstance(raw.get("acf"), Mapping) else {}
    title = raw.get("title") if isinstance(raw.get("title"), Mapping) else {}
    return SourcePost(
        post_id=int(raw["id"]),
        title=str(title.get("rendered") or ""),
        acf=dict(acf),
        tracks=parse_tracks(_rows(acf.get("music_tracks"))),
        previous_listens=parse_previous_listens(_rows(acf.get("previous-listen-posts"))),
    )


def parse_tracks(rows: Sequence[Mapping[str, Any]]) -> list[Track]:
    return [
        Track(
            disc_number=_optional_int(row.get("disc_number")),
            track_number=_optional_int(row.get("track_number")),
            title=str(row.get("title") or ""),
            duration_ms=_optional_int(row.get("duration_ms")),
            spotify_id=_optional_str(row.get("spotify_id")),
            highlight=parse_bool(row.get("highlight")),
            explicit=parse_bool(row.get("explicit")),
        )
        for row in rows
    ]


def derive_track_summary(tracks: Sequence[Track]) -> TrackSummary:
    durations = [track.duration_ms for track in tracks if track.duration_ms is not None]
    total_duration = sum(durations) if durations else None
    return TrackSummary(
        total_tracks=len(tracks),
        total_duration_ms=total_duration,
        avg_track_ms=round(total_duration / len(durations)) if durations else None,
        explicit=any(track.explicit for track in tracks),
    )


def derive_total_tracks(tracks: Sequence[Track]) -> int:
    return len(tracks)


def derive_total_length_ms(tracks: Sequence[Track]) -> int:
    return sum(track.duration_ms for track in tracks if track.duration_ms and track.duration_ms > 0)


def derive_avg_track_ms(tracks: Sequence[Track]) -> int | None:
    durations = [track.duration_ms for track in tracks if track.duration_ms and track.duration_ms > 0]
    if not durations:
        return None
    return round(sum(durations) / len(durations))


def parse_previous_listens(rows: Sequence[Mapping[str, Any]]) -> list[PreviousListen]:
    listens: list[PreviousListen] = []
    for row in rows:
        post_object = row.get("post-object")
        post_id = post_object.get("ID") if isinstance(post_object, Mapping) else post_object
        if row.get("listen-order") in (None, "") or post_id in (None, ""):
            continue
        listens.append(PreviousListen(int(row["listen-order"]), int(post_id)))
    return listens


def normalize_wp_date(value: Any) -> str | None:
    text = _optional_str(value)
    if text is None:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10].replace("-", "")
    return text


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _rows(value: Any) -> Sequence[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
