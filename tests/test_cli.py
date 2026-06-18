from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from post_to_album import cli
from post_to_album.lastfm import LastfmAlbumMatch


def _raw_post() -> dict:
    return {
        "id": 10,
        "title": {"rendered": "Artist - Album"},
        "acf": {
            "music_tracks": [
                {
                    "disc_number": "1",
                    "track_number": "1",
                    "title": "Song",
                    "duration_ms": "120000",
                    "spotify_id": "spotify-track",
                    "highlight": "0",
                }
            ],
            "lastfm_release_id": "lfm-1",
        },
        "meta": {},
        "genre": [],
    }


def _probe_response() -> dict:
    return {"id": 1, "acf": {"music_tracks": []}, "meta": {}}


def test_main_returns_zero_in_dry_run_without_writes(monkeypatch, httpx_mock, capsys):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("LASTFM_API_KEY", "")
    httpx_mock.add_response(
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[{"id": 1, "acf": {"music_tracks": []}, "meta": {}}],
    )
    httpx_mock.add_response(json=[_raw_post()])

    result = cli.main(["--limit", "1"])

    requests = httpx_mock.get_requests()
    assert result == 0
    assert [request.method for request in requests] == ["GET", "GET"]
    assert "post=10" in capsys.readouterr().out


def test_main_apply_updates_changed_posts(monkeypatch, httpx_mock):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("WORDPRESS_USERNAME", "user")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "app-pass")
    monkeypatch.setenv("LASTFM_API_KEY", "")
    httpx_mock.add_response(
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[{"id": 1, "acf": {"music_tracks": []}, "meta": {}}],
    )
    httpx_mock.add_response(json=[_raw_post()])
    httpx_mock.add_response(json={"id": 10})

    result = cli.main(["--apply", "--limit", "1"])

    requests = httpx_mock.get_requests()
    assert result == 0
    assert [request.method for request in requests] == ["GET", "GET", "POST"]
    assert requests[-1].headers["Authorization"].startswith("Basic ")
    assert json.loads(requests[-1].read())["acf"] == {
        "music_total_tracks": 1,
        "music_length_ms": 120000,
        "music_avg_track_ms": 120000,
        "music_match_confidence": "high",
        "unreleased": False,
    }


def test_main_paginates_until_empty_page(monkeypatch, httpx_mock):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("LASTFM_API_KEY", "")
    httpx_mock.add_response(
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[_probe_response()],
    )
    httpx_mock.add_response(json=[_raw_post()])
    httpx_mock.add_response(json=[{**_raw_post(), "id": 11}])
    httpx_mock.add_response(json=[])

    result = cli.main(["--batch-size", "1"])

    assert result == 0
    assert [request.url.params.get("page") for request in httpx_mock.get_requests()[1:]] == ["1", "2", "3"]


def test_main_stops_when_wordpress_reports_invalid_terminal_page(monkeypatch, httpx_mock):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("LASTFM_API_KEY", "")
    httpx_mock.add_response(
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[_probe_response()],
    )
    httpx_mock.add_response(json=[_raw_post()])
    httpx_mock.add_response(json=[{**_raw_post(), "id": 11}])
    httpx_mock.add_response(
        status_code=400,
        json={
            "code": "rest_post_invalid_page_number",
            "message": "The page number requested is larger than the number of pages available.",
        },
    )

    result = cli.main(["--batch-size", "1"])

    assert result == 0
    assert [request.url.params.get("page") for request in httpx_mock.get_requests()[1:]] == ["1", "2", "3"]


def test_main_uses_lastfm_tags_for_genre_updates(monkeypatch, httpx_mock):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("WORDPRESS_USERNAME", "user")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "app-pass")
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    fetch_lastfm = Mock(return_value=LastfmAlbumMatch("lfm-1", "Artist", "Album", ["dream-pop"]))
    monkeypatch.setattr(cli, "fetch_lastfm_album_tags", fetch_lastfm)
    httpx_mock.add_response(
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[_probe_response()],
    )
    httpx_mock.add_response(json=[_raw_post()])
    httpx_mock.add_response(json=[{"id": 7, "slug": "dream-pop"}])
    httpx_mock.add_response(json={"id": 10})

    result = cli.main(["--apply", "--limit", "1"])

    assert result == 0
    fetch_lastfm.assert_called_once()
    body = json.loads(httpx_mock.get_requests()[-1].read())
    assert body["acf"]["music_mood_tags"] == ["dream-pop"]
    assert body["genre"] == [7]


def test_main_skips_genre_update_when_existing_terms_match(monkeypatch, httpx_mock):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("WORDPRESS_USERNAME", "user")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "app-pass")
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr(
        cli,
        "fetch_lastfm_album_tags",
        Mock(return_value=LastfmAlbumMatch("lfm-1", "Artist", "Album", ["dream-pop"])),
    )
    existing = _raw_post()
    existing["genre"] = [7]
    existing["acf"].update(
        {
            "music_total_tracks": 1,
            "music_length_ms": 120000,
            "music_avg_track_ms": 120000,
            "music_match_confidence": "high",
            "music_mood_tags": ["dream-pop"],
            "unreleased": False,
        }
    )
    httpx_mock.add_response(
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[_probe_response()],
    )
    httpx_mock.add_response(json=[existing])
    httpx_mock.add_response(json=[{"id": 7, "slug": "dream-pop"}])

    result = cli.main(["--apply", "--limit", "1"])

    assert result == 0
    assert [request.method for request in httpx_mock.get_requests()] == ["GET", "GET", "GET"]


def test_main_apply_requires_credentials_before_network(monkeypatch, httpx_mock):
    monkeypatch.setenv("WORDPRESS_USERNAME", "")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "")
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")

    with pytest.raises(ValueError, match="WORDPRESS_USERNAME"):
        cli.main(["--apply"])

    assert httpx_mock.get_requests() == []


def test_main_refuses_to_send_credentials_to_http(monkeypatch, httpx_mock):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "http://example.com")
    monkeypatch.setenv("WORDPRESS_USERNAME", "user")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "app-pass")

    with pytest.raises(ValueError, match="HTTPS"):
        cli.main(["--apply"])

    assert httpx_mock.get_requests() == []


def test_main_dry_run_does_not_send_auth_header(monkeypatch, httpx_mock):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("WORDPRESS_USERNAME", "user")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "app-pass")
    httpx_mock.add_response(
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[_probe_response()],
    )
    httpx_mock.add_response(json=[])

    result = cli.main([])

    assert result == 0
    assert all("Authorization" not in request.headers for request in httpx_mock.get_requests())


def test_main_uses_process_argv_when_called_without_explicit_argv(monkeypatch, httpx_mock):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("LASTFM_API_KEY", "")
    monkeypatch.setattr("sys.argv", ["post-to-album", "--batch-size", "20", "--limit", "1"])
    httpx_mock.add_response(
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[_probe_response()],
    )
    httpx_mock.add_response(json=[_raw_post()])

    result = cli.main()

    requests = httpx_mock.get_requests()
    assert result == 0
    assert [request.method for request in requests] == ["GET", "GET"]
    assert requests[-1].url.params.get("page") == "1"
