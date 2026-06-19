import json
from pathlib import Path

import httpx

from post_to_album.lastfm import fetch_lastfm_album_tags, parse_lastfm_album_response
from post_to_album.lastfm import lastfm_album_url, normalize_lastfm_tags
from post_to_album.lastfm import parse_lastfm_release_date, score_match, spotify_album_url_from_id


def test_normalize_lastfm_tags_dedupes_and_slugifies():
    tags = normalize_lastfm_tags(["Art Pop", "art pop", "Indie  Rock", ""])

    assert tags == ["art-pop", "indie-rock"]


def test_score_match_prefers_existing_external_id():
    confidence, score = score_match("lfm-1", "lfm-1", True, True)

    assert confidence == "high"
    assert score == 100


def test_parse_lastfm_release_date_handles_iso_dd_text_and_year_only():
    assert parse_lastfm_release_date("2020-01-15") == "20200115"
    assert parse_lastfm_release_date("15 Jan 2020") == "20200115"
    assert parse_lastfm_release_date("2020") == "20200101"
    assert parse_lastfm_release_date("") is None
    assert parse_lastfm_release_date(None) is None
    assert parse_lastfm_release_date("garbage") is None


def test_spotify_album_url_from_id_builds_full_url():
    assert spotify_album_url_from_id("abc123x") == "https://open.spotify.com/album/abc123x"
    assert spotify_album_url_from_id(None) is None
    assert spotify_album_url_from_id("") is None


def test_lastfm_album_url_encodes_artist_and_album():
    assert lastfm_album_url("Artist", "Album") == "https://www.last.fm/music/Artist/Album"
    assert lastfm_album_url("Two Words", "Some Album") == "https://www.last.fm/music/Two%20Words/Some%20Album"
    assert lastfm_album_url("", "Album") is None


def test_parse_lastfm_album_response_extracts_release_id_tags_and_date():
    payload = json.loads(Path("tests/fixtures/lastfm_album.json").read_text())
    result = parse_lastfm_album_response(payload)

    assert result.release_id == "123"
    assert result.release_date == "20200115"
    assert result.tags == ["art-pop", "dream-pop"]


def test_fetch_lastfm_album_tags_calls_album_info_endpoint(httpx_mock):
    payload = json.loads(Path("tests/fixtures/lastfm_album.json").read_text())
    httpx_mock.add_response(json=payload)

    with httpx.Client() as client:
        result = fetch_lastfm_album_tags(client, "api-key", "Artist", "Album")

    request = httpx_mock.get_request()
    assert request.url.params["method"] == "album.getinfo"
    assert request.url.params["api_key"] == "api-key"
    assert request.url.params["artist"] == "Artist"
    assert request.url.params["album"] == "Album"
    assert request.url.params["format"] == "json"
    assert result is not None
    assert result.release_date == "20200115"
    assert result.tags == ["art-pop", "dream-pop"]
