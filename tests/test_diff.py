from post_to_album.diff import diff_post
from post_to_album.enrich import build_enriched_post
from post_to_album.normalize import normalize_source_post


def test_diff_post_is_noop_when_source_already_matches():
    source = normalize_source_post(
        {
            "id": 1,
            "title": {"rendered": "Album"},
            "date_gmt": "2020-01-01T00:00:00",
            "acf": {
                "music_tracks": [
                    {
                        "disc_number": 1,
                        "track_number": 1,
                        "title": "Song",
                        "duration_ms": 1000,
                        "spotify_id": "x",
                        "highlight": False,
                        "explicit": False,
                    }
                ],
                "music_total_tracks": 1,
                "music_length_ms": 1000,
                "music_avg_track_ms": 1000,
                "music_match_confidence": "high",
                "music_release_date": "20200101",
                "music_listened_at": "20200101",
                "music_explicit": False,
                "unreleased": False,
            },
        }
    )

    enriched = build_enriched_post(source, [], None, "2020-01-01", None, "high", 100)
    diff = diff_post(source, enriched)

    assert diff.changed is False
    assert diff.acf_updates == {}



def test_diff_post_emits_only_missing_derived_fields():
    source = normalize_source_post(
        {
            "id": 2,
            "title": {"rendered": "Album"},
            "date_gmt": "2024-03-03T12:00:00",
            "acf": {
                "music_tracks": [
                    {
                        "disc_number": 1,
                        "track_number": 1,
                        "title": "Song",
                        "duration_ms": 1200,
                        "spotify_id": "x",
                        "highlight": False,
                        "explicit": "1",
                    }
                ],
                "spotify_album_id": "abc123",
                "unreleased": "1",
            },
        }
    )

    enriched = build_enriched_post(source, ["art-pop"], "lfm-9", "2024-02-02", "https://www.last.fm/music/Artist/Album", "medium", 85)
    diff = diff_post(source, enriched)

    assert diff.changed is True
    assert diff.acf_updates["music_total_tracks"] == 1
    assert diff.acf_updates["music_length_ms"] == 1200
    assert diff.acf_updates["music_avg_track_ms"] == 1200
    assert diff.acf_updates["music_release_date"] == "20240202"
    assert diff.acf_updates["music_listened_at"] == "20240303"
    assert diff.acf_updates["music_explicit"] is True
    assert diff.acf_updates["music_mood_tags"] == ["art-pop"]
    assert diff.acf_updates["lastfm_release_id"] == "lfm-9"
    assert diff.acf_updates["lastfm_album_url"] == "https://www.last.fm/music/Artist/Album"
    assert diff.acf_updates["spotify_album_url"] == "https://open.spotify.com/album/abc123"
    assert diff.acf_updates["music_tracks"][0]["explicit"] is True
    assert diff.acf_updates["unreleased"] is True
    assert diff.taxonomy_updates == {"genre": ["art-pop"]}


def test_diff_post_parses_unreleased_string_zero_as_false():
    source = normalize_source_post(
        {
            "id": 3,
            "title": {"rendered": "Album"},
            "acf": {"music_tracks": [], "unreleased": "0"},
        }
    )

    enriched = build_enriched_post(source, [], None, None, None, "high", 100)
    diff = diff_post(source, enriched)

    assert enriched.acf_updates["unreleased"] is False
    assert diff.acf_updates["unreleased"] is False


def test_diff_post_skips_length_and_avg_when_no_positive_durations():
    source = normalize_source_post(
        {
            "id": 4,
            "title": {"rendered": "Album"},
            "acf": {"music_tracks": [
                {"disc_number": 1, "track_number": 1, "title": "A", "duration_ms": 0, "spotify_id": "x", "highlight": False},
                {"disc_number": 1, "track_number": 2, "title": "B", "duration_ms": None, "spotify_id": "x", "highlight": False},
            ]},
        }
    )

    enriched = build_enriched_post(source, [], None, None, None, "high", 100)

    assert enriched.acf_updates["music_total_tracks"] == 2
    assert "music_length_ms" not in enriched.acf_updates
    assert "music_avg_track_ms" not in enriched.acf_updates


def test_diff_post_does_not_set_release_date_or_listened_at_when_sources_missing():
    source = normalize_source_post(
        {
            "id": 5,
            "title": {"rendered": "Album"},
            "acf": {"music_tracks": []},
        }
    )

    enriched = build_enriched_post(source, [], None, None, None, "high", 100)

    assert "music_release_date" not in enriched.acf_updates
    assert "music_listened_at" not in enriched.acf_updates


def test_diff_post_writes_normalized_music_tracks_when_raw_shape_differs():
    raw_rows = [
        {
            "disc_number": "1",
            "track_number": "1",
            "title": "Song",
            "duration_ms": "1200",
            "spotify_id": "x",
            "highlight": "1",
        }
    ]
    source = normalize_source_post(
        {
            "id": 6,
            "title": {"rendered": "Album"},
            "acf": {"music_tracks": raw_rows},
        }
    )

    enriched = build_enriched_post(source, [], None, None, None, "high", 100)

    assert "music_tracks" in enriched.acf_updates
    row = enriched.acf_updates["music_tracks"][0]
    assert row["disc_number"] == 1
    assert row["track_number"] == 1
    assert row["duration_ms"] == 1200
    assert row["highlight"] is True


def test_diff_post_skips_music_tracks_writeback_when_already_normalized():
    normalized_rows = [
        {
            "disc_number": 1,
            "track_number": 1,
            "title": "Song",
            "duration_ms": 1200,
            "spotify_id": "x",
            "highlight": False,
            "explicit": False,
        }
    ]
    source = normalize_source_post(
        {
            "id": 7,
            "title": {"rendered": "Album"},
            "acf": {"music_tracks": normalized_rows},
        }
    )

    enriched = build_enriched_post(source, [], None, None, None, "high", 100)

    assert "music_tracks" not in enriched.acf_updates


def test_diff_post_emits_spotify_album_url_only_when_id_present_and_url_missing():
    source = normalize_source_post(
        {
            "id": 8,
            "title": {"rendered": "Album"},
            "acf": {"music_tracks": [], "spotify_album_id": "abc"},
        }
    )

    enriched = build_enriched_post(source, [], None, None, None, "high", 100)
    assert enriched.acf_updates["spotify_album_url"] == "https://open.spotify.com/album/abc"


def test_diff_post_skips_spotify_album_url_when_already_present_and_matches():
    source = normalize_source_post(
        {
            "id": 9,
            "title": {"rendered": "Album"},
            "acf": {
                "music_tracks": [],
                "spotify_album_id": "abc",
                "spotify_album_url": "https://open.spotify.com/album/abc",
            },
        }
    )

    enriched = build_enriched_post(source, [], None, None, None, "high", 100)
    assert "spotify_album_url" not in enriched.acf_updates

