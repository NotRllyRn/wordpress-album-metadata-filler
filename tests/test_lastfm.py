import json
from pathlib import Path

import httpx

from post_to_album.lastfm import fetch_lastfm_album_tags, parse_lastfm_album_response
from post_to_album.lastfm import normalize_lastfm_tags, score_match


def test_normalize_lastfm_tags_dedupes_and_slugifies():
    tags = normalize_lastfm_tags(["Art Pop", "art pop", "Indie  Rock", ""])

    assert tags == ["art-pop", "indie-rock"]


def test_score_match_prefers_existing_external_id():
    confidence, score = score_match("lfm-1", "lfm-1", True, True)

    assert confidence == "high"
    assert score == 100


def test_parse_lastfm_album_response_extracts_release_id_and_tags():
    payload = json.loads(Path("tests/fixtures/lastfm_album.json").read_text())
    result = parse_lastfm_album_response(payload)

    assert result.release_id == "123"
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
    assert result.tags == ["art-pop", "dream-pop"]
