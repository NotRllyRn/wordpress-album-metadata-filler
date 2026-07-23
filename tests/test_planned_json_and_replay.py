import io
import json
import math
import tempfile
import unittest
import urllib.error
from argparse import Namespace
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import post_to_album as mod


def patch_row(pid=1):
    return {"post_id": pid, "post_title": "Album", "source_modified": None,
            "matches": {"spotify": {"id": "s", "title": "Album", "artists": ["Artist"], "score": 1.0},
                        "lastfm": {"title": "Album", "artist": "Artist", "score": .9}},
            "write": {"acf": {"spotify_title": "Album", "music_explicit": False},
                      "categories": [93, 6],
                      "taxonomies": {"artist": ["Artist"], "release_type": ["Album"]}},
            "diagnostics": [{"code": "lastfm_no_mbid", "message": "No MBID"}]}


def plan(*rows):
    return {"schema_version": 1, "generated_at": "2026-01-01T00:00:00Z",
            "patches": list(rows or (patch_row(),))}


class FakeWP:
    def __init__(self, creation_succeeds=True):
        self.created = []
        self.updated = []
        self.creation_succeeds = creation_succeeds
    def list_tax_terms(self, tax):
        return {"Album": 30} if tax == "release_type" else {}
    def create_term(self, tax, name):
        self.created.append((tax, name)); return 10 if self.creation_succeeds else None
    def update_post(self, pid, body):
        self.updated.append((pid, body))
        if pid == 2: raise RuntimeError("failed")


class PlannedReplayTests(unittest.TestCase):
    def test_strict_validation_unknown_bool_nonfinite_duplicate_and_out_of_slice(self):
        mutations = []
        p = plan(); p["extra"] = 1; mutations.append(p)
        p = plan(); p["patches"][0]["post_id"] = True; mutations.append(p)
        p = plan(); p["patches"][0]["matches"]["spotify"]["score"] = math.nan; mutations.append(p)
        mutations.append(plan(patch_row(1), patch_row(1)))
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(ValueError): mod.validate_plan(value)
        with self.assertRaises(ValueError):
            mod.validate_plan(plan(patch_row(1), {"bad": "outside slice"}))
        for offset, limit in ((-1, None), (0, -1)):
            with self.assertRaises(ValueError): mod.slice_items([], offset, limit)

    def test_tracks_and_materialization_preserve_absent_replacements(self):
        row = patch_row(); row["write"] = {"acf": {"music_tracks": [{
            "disc_number": 1, "track_number": 1, "title": "Song", "duration_ms": 1,
            "spotify_id": "t", "highlight": False, "explicit": False}]}}
        mod.validate_plan(plan(row))
        self.assertEqual(mod.materialize_body(row["write"], {}), row["write"])
        self.assertNotIn("categories", mod.materialize_body(row["write"], {}))
        row["write"]["acf"]["music_tracks"] = []
        with self.assertRaisesRegex(ValueError, "music_tracks.*nonempty"):
            mod.validate_plan(plan(row))

    def test_resolution_once_before_updates_and_mixed_result(self):
        wp = FakeWP()
        succeeded, failed = mod.apply_patches(wp, [patch_row(1), patch_row(2)])
        self.assertEqual(wp.created, [("artist", "Artist")])
        self.assertEqual(succeeded, [1]); self.assertEqual(failed[0]["post_id"], 2)
        self.assertEqual(wp.updated[0][1]["artist"], [10])

    def test_term_resolution_failure_prevents_all_updates(self):
        wp = FakeWP(creation_succeeds=False)
        with self.assertRaises(RuntimeError): mod.apply_patches(wp, [patch_row()])
        self.assertEqual(wp.updated, [])

    def test_apply_plan_isolated_and_atomic_result(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "planned.json"; path.write_text(json.dumps(plan()))
            wp = FakeWP()
            with patch.object(mod, "WordPress", return_value=wp), \
                 patch.object(mod, "Spotify", side_effect=AssertionError), \
                 patch.object(mod, "LastFM", side_effect=AssertionError):
                rc = mod.cmd_apply_plan(Namespace(plan=str(path), offset=0, limit=None, out_dir=td), {
                    "WORDPRESS_BASE_URL": "x", "WORDPRESS_USERNAME": "u", "WORDPRESS_APP_PASSWORD": "p"})
            self.assertEqual(rc, 0)
            applied = json.loads((Path(td) / "applied.json").read_text())
            self.assertEqual(applied["succeeded"], [1])
            self.assertFalse((Path(td) / "applied.json.tmp").exists())

    def test_command_env_requirements_and_loader_does_not_warn(self):
        with self.assertRaisesRegex(SystemExit, "SPOTIFY_CLIENT_ID"):
            mod.require_env({}, "SPOTIFY_CLIENT_ID")
        with patch.object(mod.log, "warning") as warning:
            mod.load_env(None)
        warning.assert_not_called()

    def test_taxonomy_pagination_headers_fallback_and_errors(self):
        wp = object.__new__(mod.WordPress); wp._url = lambda *a, **k: "url"
        wp._req_get = lambda url: ([{"name": "A", "id": 1}], {"X-WP-TotalPages": "1"})
        self.assertEqual(wp.list_tax_terms("artist"), {"A": 1})
        wp._req_get = lambda url: ([], {})
        self.assertEqual(wp.list_tax_terms("artist"), {})

        def http_error(status, body):
            return urllib.error.HTTPError("u", status, "bad", Message(), io.BytesIO(body))

        calls = 0
        def recognized_later(url):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ([{"name": f"A{i}", "id": i + 1} for i in range(100)], {})
            raise http_error(400, b'{"code":"rest_post_invalid_page_number"}')
        wp._req_get = recognized_later
        self.assertEqual(len(wp.list_tax_terms("artist")), 100)

        for responses in (
                [http_error(400, b'{"code":"rest_post_invalid_page_number"}')],
                [([{"name": f"A{i}", "id": i + 1} for i in range(100)], {}),
                 http_error(400, b'{"code":"rest_invalid_param"}')],
                [([{"name": f"A{i}", "id": i + 1} for i in range(100)], {}),
                 http_error(400, b'not-json')],
                [http_error(500, b'{}')]):
            queue = iter(responses)
            def fail(url, queue=queue):
                value = next(queue)
                if isinstance(value, BaseException): raise value
                return value
            wp._req_get = fail
            with self.subTest(responses=responses), self.assertRaises(urllib.error.HTTPError):
                wp.list_tax_terms("artist")

    def test_run_commands_write_artifacts_and_share_replay_materialization(self):
        class CommandWP(FakeWP):
            def list_tags(self, target): return target
            def list_posts(self, per_page=100): return iter([{"id": 1}])

        env = {"WORDPRESS_BASE_URL": "x", "WORDPRESS_USERNAME": "u",
               "WORDPRESS_APP_PASSWORD": "p", "SPOTIFY_CLIENT_ID": "s",
               "SPOTIFY_CLIENT_SECRET": "ss", "LASTFM_API_KEY": "l"}
        with tempfile.TemporaryDirectory() as td:
            args = Namespace(offset=0, limit=None, out_dir=td, apply=False, dry_run=True)
            dry_wp = CommandWP()
            with patch.object(mod, "WordPress", return_value=dry_wp), \
                 patch.object(mod, "Spotify"), patch.object(mod, "LastFM"), \
                 patch.object(mod, "enrich", return_value=patch_row()):
                self.assertEqual(mod.cmd_run(args, env), 0)
            self.assertEqual(dry_wp.created, []); self.assertEqual(dry_wp.updated, [])
            saved = json.loads((Path(td) / "planned.json").read_text())
            self.assertEqual(saved["schema_version"], 1)
            self.assertTrue((Path(td) / "unresolved.json").exists())

            run_wp, replay_wp = CommandWP(), CommandWP()
            args.apply, args.dry_run = True, False
            original_materialize = mod.materialize_body
            with patch.object(mod, "WordPress", return_value=run_wp), \
                 patch.object(mod, "Spotify"), patch.object(mod, "LastFM"), \
                 patch.object(mod, "enrich", return_value=patch_row()), \
                 patch.object(mod, "materialize_body", wraps=original_materialize) as materialize:
                self.assertEqual(mod.cmd_run(args, env), 0)
                self.assertEqual(materialize.call_count, 1)
            apply_args = Namespace(plan=str(Path(td) / "planned.json"), offset=0,
                                   limit=None, out_dir=td)
            with patch.object(mod, "WordPress", return_value=replay_wp), \
                 patch.object(mod, "materialize_body", wraps=original_materialize) as materialize:
                self.assertEqual(mod.cmd_apply_plan(apply_args, env), 0)
                self.assertEqual(materialize.call_count, 1)
            self.assertEqual(run_wp.updated, replay_wp.updated)

    def test_atomic_writer_replaces_complete_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.json"; path.write_text("old")
            mod.write_json_atomic(path, {"ok": True})
            self.assertEqual(json.loads(path.read_text()), {"ok": True})
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__": unittest.main()
