import io
import json
import unittest
from email.message import Message
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import patch
from typing import Any, cast

import post_to_album as mod


SPOTIFY = {"name": "Blue - Remastered", "artists": [{"name": "Beyoncé"}]}
CANDIDATE = {"name": "Blue - Remastered", "artist": "Beyoncé"}


class SearchAndMatchingTests(unittest.TestCase):
    def test_raw_and_comparison_preserve_edition_and_accents(self):
        self.assertEqual(mod.raw_query(" A &amp; B - Deluxe "), "A & B - Deluxe")
        self.assertEqual(mod.match_key("  CAFÉ\u0301  X "), mod.match_key("CAFÉ́ x"))
        self.assertNotEqual(mod.match_key("Blue"), mod.match_key("Blue - Remastered"))
        self.assertNotEqual(mod.match_key("Beyonce"), mod.match_key("Beyoncé"))

    def test_spotify_ladder_order_dedup_and_raw_values(self):
        class Fake:
            def __init__(self): self.queries = []
            def search_albums(self, q, limit=10):
                self.queries.append((q, limit))
                return [{"id": "same"}]
        fake = Fake()
        found = mod.search_ladder(fake, "A & B - EP", ["One", "Two"])
        self.assertEqual([q for q, _ in fake.queries], [
            'album:"A & B - EP" artist:"One"', "A & B - EP One Two", "A & B - EP"])
        self.assertEqual(len(found), 1)

    def test_spotify_provider_error_propagates(self):
        class Fake:
            def search_albums(self, q, limit=10): raise urllib.error.URLError("down")
        with self.assertRaises(urllib.error.URLError):
            mod.search_ladder(Fake(), "Album", ["Artist"])

    def test_inclusive_provider_score_gates(self):
        spotify_candidate = {"id": "s", "name": "x", "artists": [{"name": "y"}]}
        lastfm_candidate = {"name": "x", "artist": "y"}
        cases = [
            (mod.choose_spotify_candidate, [spotify_candidate], "spotify_candidate_score",
             {"score": mod.SPOTIFY_MIN_SCORE, "title_score": mod.SPOTIFY_MIN_TITLE,
              "artist_score": mod.SPOTIFY_MIN_ARTIST, "candidate": spotify_candidate}),
            (mod.choose_lastfm_candidate, [lastfm_candidate], "lastfm_candidate_score",
             {"score": mod.LASTFM_MIN_SCORE, "title_score": mod.LASTFM_MIN_TITLE,
              "artist_score": mod.LASTFM_MIN_ARTIST, "candidate": lastfm_candidate}),
        ]
        for chooser, candidates, scorer, boundary in cases:
            with self.subTest(scorer=scorer), patch.object(mod, scorer, return_value=boundary):
                if chooser is mod.choose_spotify_candidate:
                    result = chooser(candidates, "not exact", ["artist"])
                else:
                    result = chooser(SPOTIFY, candidates)
                self.assertIs(result["candidate"], boundary["candidate"])

        # Each gate independently rejects a value immediately below its inclusive boundary.
        for scorer, chooser_value, base, field in [
            ("spotify_candidate_score", mod.choose_spotify_candidate,
             {"score": 1, "title_score": 1, "artist_score": 1,
              "candidate": spotify_candidate}, field)
            for field in ("title_score", "artist_score", "score")
        ] + [
            ("lastfm_candidate_score", mod.choose_lastfm_candidate,
             {"score": 1, "title_score": 1, "artist_score": 1,
              "candidate": lastfm_candidate}, field)
            for field in ("title_score", "artist_score", "score")
        ]:
            threshold = getattr(mod, ("SPOTIFY" if scorer.startswith("spotify") else "LASTFM") +
                                "_MIN_" + {"title_score": "TITLE", "artist_score": "ARTIST",
                                            "score": "SCORE"}[field])
            row = {**base, field: threshold - .001}
            chooser: Any = chooser_value
            with self.subTest(scorer=scorer, field=field), patch.object(mod, scorer, return_value=row):
                result = (chooser([base["candidate"]], "not exact", ["artist"])
                          if scorer.startswith("spotify") else chooser(SPOTIFY, [base["candidate"]]))
                self.assertIsNone(result["candidate"])

    def test_spotify_gates_missing_artist_and_ambiguity_boundary(self):
        c = {"id": "1", "name": "Album", "artists": [{"name": "Artist"}]}
        self.assertEqual(mod.choose_spotify_candidate([c], "Album", [])["reason"],
                         "spotify_missing_artist")
        c2 = {**c, "id": "2"}
        self.assertEqual(mod.choose_spotify_candidate([c, c2], "Album", ["Artist"])["reason"],
                         "spotify_ambiguous")
        with patch.object(mod, "spotify_candidate_score", side_effect=[
            {"score": .87, "title_score": .9, "artist_score": .8, "candidate": c},
            {"score": .82, "title_score": .9, "artist_score": .8, "candidate": c2},
        ]):
            # Exactly .05 is allowed: ambiguity is strictly less than the gap.
            self.assertEqual(mod.choose_spotify_candidate([c, c2], "x", ["y"])["candidate"], c)

    def test_lastfm_get_user_agent_api_error_and_malformed(self):
        class Response:
            def __init__(self, value): self.value = value
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return self.value
        seen = []
        def open_api(req, timeout):
            seen.append(req)
            return Response(b'{"error": 6, "message": "bad"}')
        with patch("urllib.request.urlopen", open_api):
            with self.assertRaisesRegex(RuntimeError, "Last.fm 6: bad"):
                mod.LastFM("key")._get("album.search", album="x")
        self.assertIn("wordpress-album-metadata-filler", seen[0].get_header("User-agent"))
        with patch("urllib.request.urlopen", return_value=Response(b"not json")):
            with self.assertRaisesRegex(RuntimeError, "malformed JSON"):
                mod.LastFM("key")._get("album.search", album="x")

    def test_lastfm_http_error_propagates(self):
        error = urllib.error.HTTPError("url", 500, "bad", Message(), None)
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                mod.LastFM("key")._get("album.search", album="x")

    def test_lastfm_search_empty_singleton_and_bad_shape(self):
        lfm = mod.LastFM("key")
        lfm._get = lambda *a, **k: {"results": {"albummatches": {"album": []}}}
        self.assertEqual(lfm.album_search("x"), [])
        lfm._get = lambda *a, **k: {"results": {"albummatches": {}}}
        self.assertEqual(lfm.album_search("x"), [])
        lfm._get = lambda *a, **k: {"results": {"albummatches": {"album": {"name": "x"}}}}
        self.assertEqual(lfm.album_search("x"), [{"name": "x"}])
        lfm._get = lambda *a, **k: {}
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            lfm.album_search("x")

    def test_getinfo_unwraps_and_uses_autocorrect_zero(self):
        lfm = mod.LastFM("key")
        calls = []
        lfm._get = lambda method, **kw: calls.append((method, kw)) or {"album": {"name": "x"}}
        self.assertEqual(lfm.album_getinfo(artist="a", album="x"), {"name": "x"})
        self.assertEqual(calls[0][1]["autocorrect"], 0)
        lfm.album_getinfo(mbid="id")
        self.assertEqual(calls[1][1], {"mbid": "id"})

    def test_lastfm_exact_and_mbid_ambiguity(self):
        self.assertEqual(mod.choose_lastfm_candidate(SPOTIFY, [CANDIDATE])["reason"], "lastfm_exact")
        duplicate = dict(CANDIDATE)
        self.assertEqual(mod.choose_lastfm_candidate(SPOTIFY, [CANDIDATE, duplicate])["reason"],
                         "lastfm_ambiguous_exact")
        mbid = "123e4567-e89b-12d3-a456-426614174000"
        pinned = {**CANDIDATE, "mbid": mbid}
        self.assertEqual(mod.choose_lastfm_candidate(SPOTIFY, [pinned, duplicate])["candidate"], pinned)

    def test_lastfm_fuzzy_ambiguity_exact_boundary(self):
        candidates = [{"name": "a"}, {"name": "b"}]
        for gap, reason in ((mod.LASTFM_MAX_TIE_GAP - .001, "lastfm_ambiguous"),
                            (mod.LASTFM_MAX_TIE_GAP, "lastfm_fuzzy")):
            with self.subTest(gap=gap), patch.object(mod, "lastfm_candidate_score", side_effect=[
                {"score": .90, "title_score": .9, "artist_score": .9, "candidate": candidates[0]},
                {"score": .90 - gap, "title_score": .9, "artist_score": .9,
                 "candidate": candidates[1]},
            ]):
                result = mod.choose_lastfm_candidate(SPOTIFY, candidates)
            self.assertEqual(result["reason"], reason)

    def test_validation_identity_tracks_and_boundaries(self):
        tracks = [{"name": "One"}, {"name": "Two"}, {"name": "Three"}, {"name": "Four"}, {"name": "Five"}]
        info = {**CANDIDATE, "tracks": {"track": []}}
        self.assertTrue(mod.validate_lastfm_info(SPOTIFY, tracks, CANDIDATE, info)["accepted"])
        info["tracks"] = {"track": [{"name": x} for x in ["One", "Two", "Three", "x", "y"]]}
        self.assertTrue(mod.validate_lastfm_info(SPOTIFY, tracks, CANDIDATE, info)["accepted"])
        info["tracks"] = {"track": [{"name": x} for x in ["One", "Two", "x", "y", "z"]]}
        self.assertEqual(mod.validate_lastfm_info(SPOTIFY, tracks, CANDIDATE, info)["reason"],
                         "lastfm_track_contradiction")
        wrong = {**info, "artist": "Someone Else", "tracks": {}}
        self.assertEqual(mod.validate_lastfm_info(SPOTIFY, tracks, CANDIDATE, wrong)["reason"],
                         "lastfm_identity_changed")

    def test_malformed_nonempty_lastfm_tracks_are_provider_errors(self):
        for tracks in ("bad", {"track": "bad"}, {"track": [{"name": "One"}, "bad"]}):
            with self.subTest(tracks=tracks), self.assertRaisesRegex(RuntimeError, "malformed"):
                mod.validate_lastfm_info(SPOTIFY, [], CANDIDATE,
                                         {**CANDIDATE, "tracks": tracks})
        for tracks in (None, {}, {"track": []}):
            with self.subTest(tracks=tracks):
                self.assertTrue(mod.validate_lastfm_info(
                    SPOTIFY, [], CANDIDATE, {**CANDIDATE, "tracks": tracks})["accepted"])

    def test_tags_reads_toptags_and_tags(self):
        self.assertEqual(mod.pick_top_tags({"toptags": {"tag": {"name": "rock"}}}, 3, []), ["rock"])
        self.assertEqual(mod.pick_top_tags({"tags": {"tag": "pop"}}, 3, []), ["pop"])

    def test_enrich_distinguishes_no_artist_provider_error_and_no_results(self):
        post = {"id": 1, "title": {"rendered": "Album"}, "date": "2020-01-01",
                "tags": [], "acf": {}}
        result = cast(dict, mod.enrich(post, object(), object(), {}, {}, object()))
        self.assertEqual(result["reason"], "spotify_missing_artist")

        class Search:
            def __init__(self, error=False): self.error = error
            def search_albums(self, *args, **kwargs):
                if self.error: raise urllib.error.URLError("down")
                return []
        post["tags"] = [7]
        result = cast(dict, mod.enrich(post, Search(error=True), object(), {7: "Artist"}, {}, object()))
        self.assertEqual(result["reason"], "spotify_provider_error")
        result = cast(dict, mod.enrich(post, Search(), object(), {7: "Artist"}, {}, object()))
        self.assertEqual(result["reason"], "spotify_no_results")

    def test_enrich_lastfm_accepted_path_and_failures(self):
        candidate = {"name": "Album", "artist": "Artist"}
        album = {"id": "sid", "name": "Album", "artists": [{"name": "Artist"}],
                 "total_tracks": 1, "album_type": "album", "release_date": "2020-01-01"}
        tracks = [{"id": "tid", "name": "Song", "duration_ms": 1000,
                   "disc_number": 1, "track_number": 1, "explicit": False}]
        post = {"id": 1, "title": {"rendered": "Album"}, "date": "2020-01-02",
                "tags": [7], "acf": {}}

        class SpotifyFake:
            def search_albums(self, *args, **kwargs):
                return [{"id": "sid", "name": "Album", "artists": [{"name": "Artist"}]}]
            def album(self, album_id): return album
            def all_tracks(self, album_id): return tracks

        class LastFmFake:
            def __init__(self, candidates=None, info=None, search_error=None):
                self.candidates = [candidate] if candidates is None else candidates
                self.info = info or {**candidate, "tracks": {},
                                     "toptags": {"tag": [{"name": "rock"}]}}
                self.search_error = search_error
                self.getinfo_calls = []
            def album_search(self, album_name, limit=10):
                if self.search_error: raise self.search_error
                return self.candidates
            def album_getinfo(self, **kwargs):
                self.getinfo_calls.append(kwargs)
                return self.info

        cache = {"artist": {"Artist": 10}, "genre": {"rock": 20},
                 "release_type": {"Album": 30, "Single": 31}}
        class WordPressFake:
            def list_tax_terms(self, tax): return cache[tax]
        wp = WordPressFake()
        for fake, reason in ((LastFmFake(search_error=RuntimeError("down")), "provider_error"),
                             (LastFmFake(candidates=[]), "lastfm_no_results")):
            with self.subTest(reason=reason):
                result = cast(dict, mod.enrich(post, SpotifyFake(), fake, {7: "Artist"}, cache, wp))
                self.assertEqual(result["reason"], reason)

        rejected = LastFmFake(info={**candidate, "artist": "Other", "tracks": {}})
        result = cast(dict, mod.enrich(post, SpotifyFake(), rejected, {7: "Artist"}, cache, wp))
        self.assertEqual(result["reason"], "lastfm_identity_changed")

        fallback = LastFmFake()
        body = cast(dict, mod.enrich(post, SpotifyFake(), fallback, {7: "Artist"}, cache, wp))
        self.assertEqual(fallback.getinfo_calls, [{"artist": "Artist", "album": "Album", "autocorrect": 0}])
        self.assertNotIn("music_mood_tags", body["acf"])
        self.assertEqual(body["genre"], [20])

        mbid_candidate = {**candidate, "mbid": "123e4567-e89b-12d3-a456-426614174000"}
        pinned = LastFmFake(candidates=[mbid_candidate])
        mod.enrich(post, SpotifyFake(), pinned, {7: "Artist"}, cache, wp)
        self.assertEqual(pinned.getinfo_calls, [{"mbid": mbid_candidate["mbid"]}])

        malformed = LastFmFake(info={**candidate, "tracks": {"track": ["bad"]}})
        result = cast(dict, mod.enrich(post, SpotifyFake(), malformed, {7: "Artist"}, cache, wp))
        self.assertEqual(result["reason"], "lastfm_provider_error")

        for track_name in (1, ["Song"], {"value": "Song"}):
            with self.subTest(track_name=track_name):
                malformed = LastFmFake(
                    info={**candidate, "tracks": {"track": [{"name": track_name}]}}
                )
                result = cast(
                    dict,
                    mod.enrich(post, SpotifyFake(), malformed, {7: "Artist"}, cache, wp),
                )
                self.assertEqual(result["reason"], "lastfm_provider_error")
                self.assertIn("malformed track name", result["details"]["error"])

    def test_cli_parser_and_fuzzy_missing_artist(self):
        args = mod.build_parser().parse_args(["fuzzy", "Album"])
        self.assertEqual(args.artists, [])
        class FakeSpotify:
            def __init__(self, *a): pass
            def search_albums(self, *a, **k): return []
        with patch.object(mod, "Spotify", FakeSpotify), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(mod.cmd_fuzzy(args, {"SPOTIFY_CLIENT_ID": "", "SPOTIFY_CLIENT_SECRET": ""}), 0)
        self.assertIn("spotify_missing_artist", output.getvalue())


if __name__ == "__main__":
    unittest.main()
