from __future__ import annotations

import ast
import json
from unittest.mock import Mock

import pytest

from post_to_album import cli, lastfm
from post_to_album.lastfm import LastfmAlbumMatch


def _raw_post(**overrides: object) -> dict:
    payload = {
        "id": 10,
        "title": {"rendered": "Artist - Album"},
        "date_gmt": "2020-01-15T10:00:00",
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
    payload.update(overrides)
    return payload


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
    acf = json.loads(requests[-1].read())["acf"]
    assert acf["music_total_tracks"] == 1
    assert acf["music_length_ms"] == 120000
    assert acf["music_avg_track_ms"] == 120000
    assert acf["music_match_confidence"] == "high"
    assert "music_release_date" not in acf
    assert acf["music_listened_at"] == "20200115"
    assert acf["unreleased"] is False


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
    fetch_lastfm = Mock(return_value=LastfmAlbumMatch("lfm-1", "Artist", "Album", "2020-02-02", ["dream-pop"]))
    monkeypatch.setattr(lastfm, "fetch_lastfm_album_tags", fetch_lastfm)
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
    assert body["acf"]["music_release_date"] == "20200202"
    assert body["acf"]["music_listened_at"] == "20200115"
    assert body["acf"]["lastfm_album_url"] == "https://www.last.fm/music/Artist/Album"
    assert body["genre"] == [7]


def test_main_skips_genre_update_when_existing_terms_match(monkeypatch, httpx_mock):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("WORDPRESS_USERNAME", "user")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "app-pass")
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr(
        cli,
        "fetch_lastfm_album_tags",
        Mock(return_value=LastfmAlbumMatch("lfm-1", "Artist", "Album", "2020-02-02", ["dream-pop"])),
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
            "music_release_date": "20200202",
            "music_listened_at": "20200115",
            "lastfm_release_id": "lfm-1",
            "lastfm_album_url": "https://www.last.fm/music/Artist/Album",
            "spotify_album_id": "spotify-track",
            "spotify_album_url": "https://open.spotify.com/album/spotify-track",
            "music_tracks": [
                {
                    "disc_number": 1,
                    "track_number": 1,
                    "title": "Song",
                    "duration_ms": 120000,
                    "spotify_id": "spotify-track",
                    "highlight": False,
                    "explicit": False,
                }
            ],
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


def test_apply_emits_complete_payload_end_to_end_with_lastfm(monkeypatch, httpx_mock):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("WORDPRESS_USERNAME", "user")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "app-pass")
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    raw = _raw_post(
        id=42,
        title={"rendered": "Artist - Album"},
        date_gmt="2025-06-15T10:00:00",
        acf={
            "music_tracks": [
                {
                    "disc_number": "1",
                    "track_number": "1",
                    "title": "Track One",
                    "duration_ms": "120000",
                    "spotify_id": "trk-1",
                    "highlight": "0",
                    "explicit": "",
                },
                {
                    "disc_number": 1,
                    "track_number": 2,
                    "title": "Track Two",
                    "duration_ms": "180000",
                    "spotify_id": "trk-2",
                    "highlight": "1",
                    "explicit": "1",
                },
            ],
            "spotify_album_id": "sp-album-9",
        },
        genre=[],
    )
    httpx_mock.add_response(
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[_probe_response()],
    )
    httpx_mock.add_response(json=[raw])
    httpx_mock.add_response(
        json={
            "album": {
                "mbid": "lfm-end2end",
                "artist": "Artist",
                "name": "Album",
                "releasedate": "2024-11-20",
                "tags": {
                    "tag": [
                        {"name": "Indie"},
                        {"name": "Art Pop"},
                    ]
                },
            }
        }
    )
    httpx_mock.add_response(json=[{"id": 11, "slug": "indie"}])
    httpx_mock.add_response(json=[{"id": 12, "slug": "art-pop"}])
    httpx_mock.add_response(json={"id": 42})

    result = cli.main(["--apply", "--limit", "1"])

    assert result == 0
    requests = httpx_mock.get_requests()
    methods_tail = [request.method for request in requests[1:]]
    assert "POST" in methods_tail

    body = json.loads(requests[-1].read())
    acf = body["acf"]
    assert acf["music_total_tracks"] == 2
    assert acf["music_length_ms"] == 300000
    assert acf["music_avg_track_ms"] == 150000
    assert acf["music_explicit"] is True
    assert acf["music_release_date"] == "20241120"
    assert acf["music_listened_at"] == "20250615"
    assert acf["music_match_confidence"] == "medium"
    assert acf["lastfm_release_id"] == "lfm-end2end"
    assert acf["lastfm_album_url"] == "https://www.last.fm/music/Artist/Album"
    assert acf["spotify_album_url"] == "https://open.spotify.com/album/sp-album-9"
    assert sorted(acf["music_mood_tags"]) == ["art-pop", "indie"]
    assert acf["unreleased"] is False
    assert acf["music_tracks"][0]["title"] == "Track One"
    assert acf["music_tracks"][0]["explicit"] is False
    assert acf["music_tracks"][1]["title"] == "Track Two"
    assert acf["music_tracks"][1]["explicit"] is True
    assert sorted(body["genre"]) == [11, 12]


def test_main_marks_low_confidence_post_as_low_confidence_skipped_no_write(monkeypatch, httpx_mock, capsys):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("WORDPRESS_USERNAME", "user")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "app-pass")
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr(
        lastfm,
        "fetch_lastfm_album_tags",
        Mock(return_value=LastfmAlbumMatch("lfm-99", "Another", "Title", "2024-01-01", ["post-punk"])),
    )
    httpx_mock.add_response(
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[_probe_response()],
    )
    httpx_mock.add_response(json=[_raw_post(title={"rendered": "Different - Words"})])

    result = cli.main(["--apply", "--limit", "1"])

    assert result == 0
    requests = httpx_mock.get_requests()
    methods = [request.method for request in requests]
    assert "POST" not in methods
    captured = capsys.readouterr().out
    assert "reasons=low-confidence-match" in captured
    assert "low-confidence-skipped" in captured


def test_main_does_not_skip_low_confidence_when_existing_id_fully_matches(monkeypatch, httpx_mock, capsys):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr(
        lastfm,
        "fetch_lastfm_album_tags",
        Mock(return_value=LastfmAlbumMatch("lfm-1", "Artist", "Album", "2020-02-02", ["dream-pop"])),
    )
    raw = _raw_post()
    raw["acf"]["lastfm_release_id"] = "lfm-1"
    raw["acf"]["music_release_date"] = "20200202"
    raw["acf"]["music_match_confidence"] = "high"
    raw["acf"]["music_mood_tags"] = ["dream-pop"]
    raw["acf"]["music_total_tracks"] = 1
    raw["acf"]["music_length_ms"] = 120000
    raw["acf"]["music_avg_track_ms"] = 120000
    raw["acf"]["unreleased"] = False
    raw["acf"]["music_listened_at"] = "20200115"
    raw["acf"]["spotify_album_url"] = None
    httpx_mock.add_response(
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[_probe_response()],
    )
    httpx_mock.add_response(json=[raw])

    result = cli.main(["--limit", "1"])

    assert result == 0
    posts_calls = [
        request for request in httpx_mock.get_requests() if request.url.path.endswith("/posts")
    ]
    assert "low-confidence-match" not in capsys.readouterr().out


def test_dry_run_message_lists_all_new_derived_fields(monkeypatch, httpx_mock, capsys):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr(
        lastfm,
        "fetch_lastfm_album_tags",
        Mock(return_value=LastfmAlbumMatch("lfm-9", "Artist", "Album", "2024-11-20", ["indie"])),
    )
    raw = _raw_post(
        acf={
            "music_tracks": [
                {
                    "disc_number": "1",
                    "track_number": "1",
                    "title": "Track",
                    "duration_ms": "100000",
                    "spotify_id": "t",
                    "highlight": "0",
                    "explicit": "",
                }
            ],
            "spotify_album_id": "sp-9",
        }
    )
    httpx_mock.add_response(
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[_probe_response()],
    )
    httpx_mock.add_response(json=[raw])

    result = cli.main(["--limit", "1"])

    assert result == 0
    captured = capsys.readouterr().out
    updates_line = next(line for line in captured.splitlines() if line.startswith("post="))
    listed = updates_line.split("updates=", 1)[1]
    listed_keys = set(ast.literal_eval(listed))
    expected = {
        "music_total_tracks",
        "music_length_ms",
        "music_avg_track_ms",
        "music_listened_at",
        "spotify_album_url",
        "lastfm_album_url",
        "music_mood_tags",
        "music_match_confidence",
        "music_tracks",
        "unreleased",
    }
    assert expected.issubset(listed_keys)

