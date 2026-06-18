from __future__ import annotations

from dataclasses import dataclass

import httpx


LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"


@dataclass(frozen=True, slots=True)
class LastfmAlbumMatch:
    release_id: str | None
    artist: str
    album: str
    tags: list[str]


def normalize_lastfm_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for tag in tags:
        value = "-".join(tag.strip().lower().split())
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def score_match(
    existing_lastfm_id: str | None,
    match_id: str | None,
    artist_exact: bool,
    album_exact: bool,
) -> tuple[str, int]:
    if existing_lastfm_id and match_id and existing_lastfm_id == match_id:
        return "high", 100
    if artist_exact and album_exact:
        return "medium", 85
    return "low", 40


def parse_lastfm_album_response(payload: dict) -> LastfmAlbumMatch:
    album = payload["album"]
    raw_tags = [row["name"] for row in album.get("tags", {}).get("tag", [])]
    return LastfmAlbumMatch(
        release_id=album.get("mbid") or None,
        artist=album.get("artist") or "",
        album=album.get("name") or "",
        tags=normalize_lastfm_tags(raw_tags),
    )


def fetch_lastfm_album_tags(
    client: httpx.Client,
    api_key: str,
    artist: str,
    album: str,
) -> LastfmAlbumMatch | None:
    response = client.get(
        LASTFM_API_URL,
        params={
            "method": "album.getinfo",
            "api_key": api_key,
            "artist": artist,
            "album": album,
            "format": "json",
        },
    )
    response.raise_for_status()
    payload = response.json()
    if "album" not in payload:
        return None
    return parse_lastfm_album_response(payload)
