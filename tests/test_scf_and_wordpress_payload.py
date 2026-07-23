import unittest
from typing import cast
from unittest.mock import patch

import post_to_album as mod


class SpotifyFake:
    album_data = {
        "id": "album-id", "name": "Élan (Deluxe Edition) - Single",
        "artists": [{"name": "The Artist"}], "total_tracks": 2,
        "album_type": "single", "release_date": "2024-02-03",
    }
    tracks = [
        {"id": "one", "name": "First – Edit", "duration_ms": 1000,
         "disc_number": 1, "track_number": 1, "explicit": False},
        {"id": "two", "name": "Second", "duration_ms": 2000,
         "disc_number": 1, "track_number": 2, "explicit": False},
    ]

    def search_albums(self, *args, **kwargs):
        return [{"id": "album-id", "name": "Post Title",
                 "artists": [{"name": "The Artist"}]}]

    def album(self, album_id):
        return dict(self.album_data)

    def all_tracks(self, album_id):
        return list(self.tracks)


class LastFmFake:
    def album_search(self, *args, **kwargs):
        return [{"name": SpotifyFake.album_data["name"], "artist": "The Artist"}]

    def album_getinfo(self, **kwargs):
        return {"name": SpotifyFake.album_data["name"], "artist": "The Artist",
                "tracks": {}, "toptags": {"tag": [
                    {"name": "THE ARTIST"}, {"name": "Rock"},
                    {"name": "rock"}, {"name": "Pop"}, {"name": "Ambient"},
                    {"name": "Jazz"}]}}


class WordPressFake:
    def __init__(self):
        self.resolved = []
        self.ids = {"The Artist": 10, "Rock": 20, "Pop": 21,
                    "Ambient": 22, "Single": 30}

    def list_tax_terms(self, tax):
        self.resolved.append(("list", tax))
        return {}

    def create_term(self, tax, name) -> int | None:
        self.resolved.append((tax, name))
        return self.ids[name]


def make_post(**changes):
    post = {"id": 1, "title": {"rendered": "Post Title"},
            "date": "2024-03-04", "tags": [7], "acf": {},
            "artist": [], "genre": [], "release_type": [],
            "categories": [93, 6, 200, 42, 93]}
    post.update(changes)
    return post


def enrich(post, wp=None):
    wp = wp or WordPressFake()
    return cast(dict, mod.enrich(
        post, SpotifyFake(), LastFmFake(), {7: "The Artist"},
        {"artist": {}, "genre": {}, "release_type": {}}, wp))


class PayloadTests(unittest.TestCase):
    def test_approved_auto_fields_exactly(self):
        self.assertEqual(mod.AUTO_FILLABLE_FIELDS, (
            "spotify_title", "music_tracks", "music_length_ms",
            "spotify_album_id", "spotify_album_url", "music_release_date",
            "music_listened_at", "lastfm_release_id", "music_total_tracks",
            "music_avg_track_ms", "music_explicit", "listen_count"))

    def test_payload_raw_title_keys_false_highlights_categories_and_taxonomies(self):
        post = make_post(acf={"music_tracks": [
            {"spotify_id": "one", "highlight": True},
            {"spotify_id": "old", "highlight": True}]})
        body = enrich(post)
        acf = body["acf"]
        self.assertEqual(acf["spotify_title"], "Élan (Deluxe Edition) - Single")
        self.assertEqual(acf["music_explicit"], False)
        self.assertEqual(acf["listen_count"], 1)
        self.assertNotIn("listen-count", acf)
        self.assertNotIn("music_mood_tags", acf)
        self.assertNotIn("unreleased", acf)
        self.assertNotIn("music_tracks", acf)  # existing provider rows are fill-only
        # Category order is preserved, legacy release IDs replaced, and duplicates removed.
        self.assertEqual(body["categories"], [93, 200, 42, 5])
        self.assertEqual(body["artist"], [10])
        self.assertEqual(body["genre"], [20, 21, 22])
        self.assertEqual(body["release_type"], [30])

    def test_rebuilt_tracks_preserve_highlight_by_spotify_id(self):
        post = make_post(acf={"music_tracks": [
            {"spotify_id": "one", "highlight": True}]})
        original = mod.is_field_present

        def destination_presence(field, value):
            # Simulate a schema adapter reporting this repeater as replaceable.
            return False if field == "music_tracks" else original(field, value)

        with patch.object(mod, "is_field_present", side_effect=destination_presence):
            rows = enrich(post)["acf"]["music_tracks"]
        self.assertTrue(rows[0]["highlight"])
        self.assertFalse(rows[1]["highlight"])
        self.assertEqual([row["spotify_id"] for row in rows], ["one", "two"])

    def test_fill_only_acf_and_editorial_fields_untouched(self):
        acf = {"spotify_title": "Editor title", "music_rating": 5,
               "music_favorite": True, "music_notes": "keep"}
        body = enrich(make_post(acf=acf))
        self.assertNotIn("spotify_title", body["acf"])
        for key in ("music_rating", "music_favorite", "music_notes"):
            self.assertNotIn(key, body["acf"])

    def test_missing_optional_provider_values_are_omitted(self):
        old_date = SpotifyFake.album_data["release_date"]
        SpotifyFake.album_data["release_date"] = "bad"
        try:
            body = enrich(make_post())
        finally:
            SpotifyFake.album_data["release_date"] = old_date
        self.assertNotIn("music_release_date", body["acf"])
        self.assertNotIn("lastfm_release_id", body["acf"])

    def test_existing_artist_and_genre_are_omitted_without_resolution(self):
        wp = WordPressFake()
        body = enrich(make_post(artist=[99], genre=[98]), wp)
        self.assertNotIn("artist", body)
        self.assertNotIn("genre", body)
        self.assertFalse(any(tax in ("artist", "genre") for tax, _ in wp.resolved))
        self.assertEqual(body["release_type"], [30])

    def test_completion_requires_artist_and_release_type_not_genre(self):
        acf = {name: (False if name == "music_explicit" else 1)
               for name in mod.AUTO_FILLABLE_FIELDS}
        self.assertTrue(mod.post_is_complete(
            make_post(acf=acf, artist=[1], genre=[], release_type=[2])))
        self.assertFalse(mod.post_is_complete(make_post(acf=acf, release_type=[2])))
        self.assertFalse(mod.post_is_complete(make_post(acf=acf, artist=[1])))

    def test_unresolved_release_type_returns_no_partial_payload(self):
        class MissingReleaseType(WordPressFake):
            def create_term(self, tax, name) -> int | None:
                self.resolved.append((tax, name))
                return None if tax == "release_type" else self.ids[name]

        body = enrich(make_post(), MissingReleaseType())
        self.assertEqual(body, {
            "__unresolved__": True,
            "reason": "release_type_unresolved",
            "details": {"release_type": "Single"},
            "candidates": [],
        })

    def test_genre_filter_is_case_insensitive_deduped_ordered_and_capped(self):
        info = {"toptags": {"tag": ["Seen Live", "Artist", "Rock", "rock",
                                     "Pop", "Ambient", "Jazz"]}}
        self.assertEqual(mod.pick_top_tags(info, 3, mod.LFM_BLOCKLIST, ["artist"]),
                         ["Rock", "Pop", "Ambient"])
        self.assertEqual(mod.pick_top_tags({"toptags": {"tag": ["AOTY", "Artist"]}},
                                           3, mod.LFM_BLOCKLIST, ["artist"]), [])

    def test_no_accepted_genres_emits_no_genre_and_creates_no_unknown(self):
        class EmptyGenres(LastFmFake):
            def album_getinfo(self, **kwargs):
                data = super().album_getinfo(**kwargs)
                data["toptags"] = {"tag": ["AOTY", "The Artist"]}
                return data
        wp = WordPressFake()
        body = cast(dict, mod.enrich(
            make_post(), SpotifyFake(), EmptyGenres(), {7: "The Artist"},
            {"artist": {}, "genre": {}, "release_type": {}}, wp))
        self.assertNotIn("genre", body)
        self.assertNotIn(("genre", "Unknown"), wp.resolved)


if __name__ == "__main__":
    unittest.main()
