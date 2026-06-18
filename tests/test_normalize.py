from post_to_album.models import PreviousListen, Track
from post_to_album.normalize import (
    derive_avg_track_ms,
    derive_track_summary,
    derive_total_length_ms,
    derive_total_tracks,
    normalize_source_post,
    normalize_wp_date,
    parse_previous_listens,
    parse_tracks,
)


def test_parse_tracks_and_derive_summary_from_acf_rows():
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
    summary = derive_track_summary(tracks)

    assert tracks == [
        Track(1, 1, "First", 1000, "track-1", True, False),
        Track(1, 2, "Second", 3000, "track-2", False, True),
    ]
    assert summary.total_tracks == 2
    assert summary.total_duration_ms == 4000
    assert summary.avg_track_ms == 2000
    assert summary.explicit is True


def test_parse_previous_listens_and_normalize_wordpress_dates():
    rows = [
        {"listen-order": "1", "post-object": 123},
        {"listen-order": 2, "post-object": {"ID": "456"}},
    ]

    assert parse_previous_listens(rows) == [
        PreviousListen(1, 123),
        PreviousListen(2, 456),
    ]
    assert normalize_wp_date("2026-06-18T12:34:56") == "20260618"
    assert normalize_wp_date("20260618") == "20260618"
    assert normalize_wp_date("") is None


def test_normalize_source_post_reads_tracks_and_relistens():
    source = normalize_source_post(
        {
            "id": 9,
            "title": {"rendered": "Album"},
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
    assert source.acf["music_tracks"]
    assert source.tracks[0].title == "Song"
    assert source.previous_listens[0].post_id == 77


def test_track_aggregate_wrappers_ignore_unknown_durations_for_length_and_average():
    tracks = [
        Track(1, 1, "A", 200000, "sp1", False, False),
        Track(1, 2, "B", None, "sp2", False, False),
        Track(1, 3, "C", 100000, "sp3", True, True),
    ]

    assert derive_total_tracks(tracks) == 3
    assert derive_total_length_ms(tracks) == 300000
    assert derive_avg_track_ms(tracks) == 150000
