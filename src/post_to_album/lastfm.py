from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx

from post_to_album.models import LastfmMatch


LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"


@dataclass(frozen=True, slots=True)
class LastfmAlbumMatch:
    release_id: str | None
    artist: str
    album: str
    release_date: str | None
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


def parse_lastfm_release_date(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        prefix = text[:10]
        day = prefix[8:]
        if day == "00":
            return f"{prefix[:4]}{prefix[5:7]}01"
        if prefix[:4].isdigit() and prefix[5:7].isdigit() and day.isdigit():
            return prefix.replace("-", "")
        return None
    if len(text) == 4 and text.isdigit():
        return f"{text}0101"
    parts = text.split(" ")
    if len(parts) == 3:
        from datetime import datetime

        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(text, fmt).strftime("%Y%m%d")
            except ValueError:
                continue
    return None


def parse_lastfm_album_response(payload: dict) -> LastfmAlbumMatch:
    album = payload["album"]
    raw_tags = [row["name"] for row in album.get("tags", {}).get("tag", [])]
    return LastfmAlbumMatch(
        release_id=album.get("mbid") or None,
        artist=album.get("artist") or "",
        album=album.get("name") or "",
        release_date=parse_lastfm_release_date(album.get("releasedate")),
        tags=normalize_lastfm_tags(raw_tags),
    )


def spotify_album_url_from_id(spotify_id: str | None) -> str | None:
    text = (spotify_id or "").strip()
    if not text:
        return None
    return f"https://open.spotify.com/album/{text}"


def lastfm_album_url(artist: str, album: str) -> str | None:
    if not (artist.strip() and album.strip()):
        return None
    return f"https://www.last.fm/music/{quote(artist.strip())}/{quote(album.strip())}"


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


def split_artist_album(title: str) -> tuple[str, str]:
    if " - " not in title:
        return "", title.strip()
    artist, album = title.split(" - ", 1)
    return artist.strip(), album.strip()


def match_source(
    title: str,
    existing_lastfm_id: str | None,
    client: httpx.Client,
    api_key: str | None,
) -> LastfmMatch:
    artist, album = split_artist_album(title)
    if not (api_key and artist and album):
        confidence, score = score_match(existing_lastfm_id, existing_lastfm_id, True, True)
        return LastfmMatch(None, None, [], confidence, score)
    match = fetch_lastfm_album_tags(client, api_key, artist, album)
    if match is None:
        confidence, score = score_match(existing_lastfm_id, existing_lastfm_id, True, True)
        return LastfmMatch(None, None, [], confidence, score)
    confidence, score = score_match(
        existing_lastfm_id,
        match.release_id,
        artist.casefold() == match.artist.casefold(),
        album.casefold() == match.album.casefold(),
    )
    return LastfmMatch(match.release_id, match.release_date, match.tags, confidence, score)
