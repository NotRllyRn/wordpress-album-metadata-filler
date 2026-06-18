from post_to_album.diff import build_enriched_post, diff_post
from post_to_album.normalize import normalize_source_post


def test_diff_post_is_noop_when_source_already_matches():
    source = normalize_source_post(
        {
            "id": 1,
            "title": {"rendered": "Album"},
            "acf": {
                "music_tracks": [
                    {
                        "disc_number": 1,
                        "track_number": 1,
                        "title": "Song",
                        "duration_ms": 1000,
                        "spotify_id": "x",
                        "highlight": False,
                    }
                ],
                "music_total_tracks": 1,
                "music_length_ms": 1000,
                "music_avg_track_ms": 1000,
                "music_match_confidence": "high",
                "unreleased": False,
            },
        }
    )

    enriched = build_enriched_post(source, [], "high", 100)
    diff = diff_post(source, enriched)

    assert diff.changed is False
    assert diff.acf_updates == {}


def test_diff_post_emits_only_missing_derived_fields():
    source = normalize_source_post(
        {
            "id": 2,
            "title": {"rendered": "Album"},
            "acf": {
                "music_tracks": [
                    {
                        "disc_number": 1,
                        "track_number": 1,
                        "title": "Song",
                        "duration_ms": 1200,
                        "spotify_id": "x",
                        "highlight": False,
                    }
                ],
                "unreleased": "1",
            },
        }
    )

    enriched = build_enriched_post(source, ["art-pop"], "medium", 85)
    diff = diff_post(source, enriched)

    assert diff.changed is True
    assert diff.acf_updates["music_total_tracks"] == 1
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

    enriched = build_enriched_post(source, [], "high", 100)
    diff = diff_post(source, enriched)

    assert enriched.acf_updates["unreleased"] is False
    assert diff.acf_updates["unreleased"] is False
