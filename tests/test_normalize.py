from post_to_album.models import PreviousListen, Track
from post_to_album.normalize import (
    derive_avg_track_ms,
    derive_total_length_ms,
    derive_total_tracks,
    normalize_source_post,
    normalize_wp_date,
    parse_previous_listens,
    parse_tracks,
    to_wp_date_yyyymmdd,
)


def test_parse_tracks_and_derive_aggregates_from_acf_rows():
    rows = [
        {
            "disc_number": "1",
            "track_number": "1",
            "title": "First",
            "duration_ms": "1000",
            "spotify_id": "track-1",
            "highlight": "1",
            "explicit": "0",
        },
        {
            "disc_number": 1,
            "track_number": 2,
            "title": "Second",
            "duration_ms": 3000,
            "spotify_id": "track-2",
            "highlight": False,
            "explicit": True,
        },
    ]

    tracks = parse_tracks(rows)

    assert tracks[0].title == "First"
    assert tracks[0].explicit is False
    assert tracks[1].explicit is True
    assert derive_total_tracks(tracks) == 2
    assert derive_total_length_ms(tracks) == 4000
    assert derive_avg_track_ms(tracks) == 2000


def test_parse_previous_listens_supports_string_and_dict_post_object():
    rows = [
        {"listen-order": "1", "post-object": 123},
        {"listen-order": 2, "post-object": {"ID": "456"}},
        {"listen-order": "3", "post-object": None},
        {"listen-order": "", "post-object": 999},
    ]

    assert parse_previous_listens(rows) == [
        PreviousListen(1, 123),
        PreviousListen(2, 456),
    ]


def test_normalize_wp_date_handles_iso_and_yyyymmdd():
    assert normalize_wp_date("2026-06-18T12:34:56") == "20260618"
    assert normalize_wp_date("20260618") == "20260618"
    assert normalize_wp_date("") is None
    assert normalize_wp_date(None) is None


def test_to_wp_date_yyyymmdd_returns_8_digit_form():
    assert to_wp_date_yyyymmdd("2026-06-18T12:34:56") == "20260618"
    assert to_wp_date_yyyymmdd("20260618") == "20260618"
    assert to_wp_date_yyyymmdd("") is None
    assert to_wp_date_yyyymmdd(None) is None


def test_normalize_source_post_captures_published_at_from_date_gmt():
    source = normalize_source_post(
        {
            "id": 9,
            "title": {"rendered": "Album"},
            "date_gmt": "2024-05-05T10:00:00",
            "acf": {
                "music_tracks": [
                    {
                        "disc_number": "1",
                        "track_number": "2",
                        "title": "Song",
                        "duration_ms": "123",
                        "spotify_id": "abc",
                        "highlight": 1,
                    }
                ],
                "previous-listen-posts": [{"listen-order": "3", "post-object": "77"}],
            },
        }
    )

    assert source.post_id == 9
    assert source.title == "Album"
    assert source.tracks[0].title == "Song"
    assert source.previous_listens[0].post_id == 77
    assert source.published_at == "2024-05-05T10:00:00"


def test_normalize_source_post_falls_back_to_date_when_date_gmt_missing():
    source = normalize_source_post(
        {
            "id": 10,
            "title": {"rendered": "Album"},
            "date": "2024-06-06T10:00:00",
            "acf": {"music_tracks": []},
        }
    )

    assert source.published_at == "2024-06-06T10:00:00"


def test_normalize_source_post_published_at_none_when_missing():
    source = normalize_source_post(
        {
            "id": 11,
            "title": {"rendered": "Album"},
            "acf": {"music_tracks": []},
        }
    )

    assert source.published_at is None
