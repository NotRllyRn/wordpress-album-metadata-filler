# WordPress Metadata Backfill — Derived Fields Plan (rewrite)

> Replaces plan `2026-06-18-wordpress-metadata-backfill.md` after implementation drifted from spec. Captures the actual field semantics and module shape the script now produces. Run `python -m pytest -v` (49 expected) to verify, then graphify-update to refresh the knowledge graph.

**Goal:** One-time Python CLI that dry-runs then backfills old WordPress release posts with normalized custom metadata for frontend archive/search use, with 12 deterministic or externally-derived ACF fields, no unsafe overwrite, idempotent reruns, and confidence-gated Last.fm writes.

**Architecture:** Pure-core separation from transport edge.

| Layer | Module | Pure? | Inputs | Outputs |
|---|---|---|---|---|
| Config boundary | `src/post_to_album/config.py` | yes | env, argv flags | `Config` |
| Source reader | `src/post_to_album/wordpress_api.py` | side-effect (httpx) | base_url, auth, page, per_page | list of raw WP post dicts |
| Probe gate | `src/post_to_album/wordpress_api.py` | side-effect | base_url, auth, timeout | `ProbeResult(supports_acf_reads, supports_target_shape)` |
| Source normalize | `src/post_to_album/normalize.py` | yes | raw WP dict | `SourcePost` (tracks, listens, published_at) |
| Enrichment engine | `src/post_to_album/enrich.py` | yes | `SourcePost`, lastfm args | `EnrichedPost` |
| Last.fm fetch | `src/post_to_album/lastfm.py` | side-effect (httpx) | client, api_key, artist, album | `LastfmAlbumMatch` incl `release_date` |
| Last.fm match orchestration | `src/post_to_album/lastfm.py` (`match_source`) | side-effect | title, existing.lastfm_id, client, api_key | `LastfmMatch` (id, date, tags, confidence, score) |
| Diff planner | `src/post_to_album/diff.py` | yes | `SourcePost`, `EnrichedPost` | `DiffResult` |
| Update writer | `src/post_to_album/wordpress_api.py` (`update_post`) | side-effect | base_url, auth, post_id, payload | WP response |
| Taxonomy resolver | `src/post_to_album/wordpress_api.py` (`resolve_taxonomies_for_post`) | side-effect | base_url, raw_post, taxonomy_updates | dict of slug -> term_ids |
| Reporter | `src/post_to_album/report.py` | yes | post_id, reasons, updates | dry-run row string + summary dict |
| CLI orchestrator | `src/post_to_album/cli.py` | side-effect for apply | argv | int exit code |

**Tech stack:** Python 3.13, `pytest`, `pytest-httpx`, `httpx`, `python-dotenv`, stdlib `dataclasses`. `urllib.parse.quote` for last.fm URL encoding. WordPress REST API, Last.fm API. No Spotify integration — Spotify fields derive from `spotify_album_id` (user-entered) into `spotify_album_url`.

---

## Field contract

### Derive deterministically from source post (always emit if source has the data)

| Field | Source | Helper | Emit condition |
|---|---|---|---|
| `music_total_tracks` | `source.tracks` | `track_stats_update` | any track present |
| `music_length_ms` | positive durations in `source.tracks` | `track_stats_update` | at least one duration > 0 |
| `music_avg_track_ms` | same | `track_stats_update` | at least one duration > 0 |
| `music_explicit` | any `track.explicit` | `track_stats_update` | any track explicit |
| `music_listened_at` | `source.published_at` (preferring `date_gmt` over `date`) | `listen_event_date_update` | published_at present, parsed to `YYYYMMDD` |
| `music_tracks` | normalized rows | `tracks_writeback_update` | any track present AND shape differs from source-acf shape |
| `spotify_album_url` | `source.acf["spotify_album_id"]` | `spotify_album_url_update` | id non-empty AND `spotify_album_url` not already equal to derived URL |
| `unreleased` | `source.acf["unreleased"]` (parsed) | `unreleased_flag_update` | always — preserved/normalized |

### Derive from Last.fm (gated)

| Field | Source | Emit condition |
|---|---|---|
| `music_release_date` | `lastfm.album.releasedate` (parsed ISO / "15 Jan 2020" / "2020") via `parse_lastfm_release_date` → `YYYYMMDD` | lastfm lookup succeeded AND date parsed AND confidence != "low" |
| `music_match_confidence` | `lastfm.match_source` confidence | always when not skipped |
| `lastfm_release_id` | `lastfm.album.mbid` | non-empty |
| `lastfm_album_url` | `f"https://www.last.fm/music/{quote(artist)}/{quote(album)}"` from `split_artist_album(title)` | release_id set AND title has ` - ` AND confidence != "low" |
| `music_mood_tags` | `lastfm.tags` (slugified via `normalize_lastfm_tags`) + `genre` taxonomy | tags non-empty |

### Trust boundary (never overwrite)

User-entered fields consolidated from `source.acf` and emitted by diff only if missing OR shape differs:

`music_rating`, `music_favorite`, `spotify_album_id`, `music_listened_at`, `music_label`, `music_notes`, `music_source`, `music_review_status`, `previous-listen-posts`, `music_tracks`.

### Idempotence rule (diff planner)

`diff_post` emits `acf_updates[k] = v` only if `source.acf.get(k) != v`. Stable shape = no diff. Re-runs are no-op.

---

## Confidence gate

Spec: "Low-confidence result -> no metadata overwrite. Report only."

`LastfmMatch.confidence` levels:

- `high` = `existing_lastfm_id == match_id` (both non-empty)
- `medium` = artist+album canonicalized exact match
- `low` = fuzzy only

CLI behavior on `confidence == "low"`:

1. Print `post=N reasons=low-confidence-match updates=[]`
2. Append `("low-confidence-skipped", post_id)` to status rows
3. Skip enrichment + diff + tax lookup + write entirely
4. Summary counter `low-confidence-skipped` shows in final report

Reason codes seen in dry-run rows:

- `already-normalized` — diff empty
- `field-diff` — diff has fields
- `low-confidence-match` — confidence == low, skipped before enrichment

---

## File structure

```
pyproject.toml                  deps + pytest config
src/post_to_album/
  __init__.py
  config.py                     Config + load_config
  models.py                     Track (+ to_dict), TrackSummary, PreviousListen,
                               SourcePost, EnrichedPost, LastfmMatch
  normalize.py                  parse_tracks, parse_previous_listens,
                               to_wp_date_yyyymmdd, _resolve_published_at,
                               derive_* aggregate helpers, parse_bool
  enrich.py                     build_enriched_post,
                               plus per-field helpers:
                               track_stats_update, tracks_writeback_update,
                               release_date_update, listen_event_date_update,
                               spotify_album_url_update,
                               match_metadata_update, unreleased_flag_update
  diff.py                       DiffResult + diff_post (comparator only)
  lastfm.py                     parse_lastfm_release_date, normalize_lastfm_tags,
                               score_match, parse_lastfm_album_response,
                               fetch_lastfm_album_tags, split_artist_album,
                               match_source, spotify_album_url_from_id,
                               lastfm_album_url (URL-encoded via urllib.parse.quote)
  wordpress_api.py              ProbeResult + probe_wordpress_api,
                               ensure_https (HTTPS-only apply gate),
                               build_basic_auth_header, auth_header_for_apply,
                               fetch_posts_page, resolve_taxonomies_for_post,
                               _raise_on_bad_status, WordpressWriteError,
                               update_post
  report.py                     format_dry_run_row, summarize_counts
  cli.py                        main(argv) — orchestration + confidence gate
README.md                       setup + env vars + dry-run + apply + tests
tests/
  test_config.py
  test_normalize.py             includes published_at date_gmt/date/fallback coverage
  test_diff.py                  no-op + partial + writeback + url-only + url-skip
  test_lastfm.py                parse tags/url, score, release_date variants,
                               fetch endpoint, fixture parse with release_date
  test_wordpress_api.py         _fields includes date,date_gmt; auth; update shape;
                               taxonomy helper
  test_report.py
  test_cli.py                   12 tests: dry-run, apply, paginate,
                               invalid-page, lastfm tags, existing-match,
                               credentials, https refusal, auth header,
                               process argv, END-TO-END full payload,
                               dry-run message keys, low-confidence skip,
                               low-confidence existing-match survives
  fixtures/lastfm_album.json    now includes "releasedate": "15 Jan 2020"
graphify-out/
  graph.html                    219 nodes · 445 edges · 11 communities
  GRAPH_REPORT.md               generated by `/graphify --update .`
```

---

## Task 1: Verify scaffold + config boundaries match implementation

**Steps:**

1. `python -m pytest tests/test_config.py -v` → PASS (4 tests)
2. `python -m pytest tests/test_wordpress_api.py -v` → PASS (6 tests)
3. Confirm `Config.wordpress_base_url`, `lastfm_api_key` validations live in `config.py`

**Accept:** All 4 config tests + 6 wp-api tests pass, no probe regression.

---

## Task 2: Verify normalization + source model

**Steps:**

1. `python -m pytest tests/test_normalize.py -v` → PASS (7 tests)
2. Confirm `source.published_at` resolves from `date_gmt` first, `date` fallback, `None` when both missing
3. Confirm `parse_bool` handles string `"0"`, `"1"`, `"true"`, `"yes"`, native bool

**Accept:** 7 normalize tests pass; `published_at` semantics correct on all 3 fixtures.

---

## Task 3: Verify Last.fm enrichment + release_date parsing + url helpers

**Steps:**

1. `python -m pytest tests/test_lastfm.py -v` → PASS (7 tests)
2. Confirm `parse_lastfm_release_date` accepts ISO, dd-Mon-yyyy, year-only and rejects garbage
3. Confirm `spotify_album_url_from_id` returns full URL for non-empty id, None for empty/None
4. Confirm `lastfm_album_url` URL-encodes spaces via `urllib.parse.quote`

**Accept:** 7 lastfm tests pass; release_date path produces `YYYYMMDD` in all valid formats.

---

## Task 4: Verify diff + enrich semantics

**Steps:**

1. `python -m pytest tests/test_diff.py -v` → PASS (10 tests)
2. Confirm `diff_post` is no-op when source already matches
3. Confirm `tracks_writeback_update` no-ops when normalized rows equal raw shape, emits when different
4. Confirm `spotify_album_url_update` no-ops when URL already correct

**Accept:** 10 diff tests pass; new-derived-field semantics honored (release from lastfm, listened_at from post date, url from id).

---

## Task 5: Verify full end-to-end shape (httpx_mock)

**Steps:**

1. `python -m pytest tests/test_cli.py -v` → PASS (12 tests)
2. `test_apply_emits_complete_payload_end_to_end_with_lastfm` asserts all 5 new fields + normalized tracks + mood tags + genres in final POST body
3. `test_dry_run_message_lists_all_new_derived_fields` (via `ast.literal_eval`) asserts dry-run message contains every new derived key
4. `test_main_marks_low_confidence_post_as_low_confidence_skipped_no_write` → POST absent, reason + counter present
5. `test_main_does_not_skip_low_confidence_when_existing_id_fully_matches` → sanity gate (existing fully-canonical stays on normal path)

**Accept:** 12 cli tests pass; merged full payload matches expected; confidence gate prevents overwrites.

---

## Task 6: Refresh knowledge graph + verify README

**Steps:**

1. `python -m graphify update .` → expect 219 nodes / 445 edges
2. Update `README.md` to reflect: 5 derived fields + low-confidence gate + run path

**Accept:** Graph regenerated; README references current field semantics.

---

## Open implementer notes

- `cli.main` short-circuits on low confidence BEFORE `enrich.build_enriched_post` so even derivable fields (track stats, listened_at) are not emitted. Spec wanted to avoid any overwrites, so a fully-correct post could still report `already-normalized` on re-run; that's a tradeoff. If preserving local-derived fields on low-confidence becomes a requirement, gate moves from CLI into `build_enriched_post` and selected helpers skip on low.
- Spotify lookup (real artist→album search) not yet integrated. Currently Spotify URL is constructed from existing `spotify_album_id` only — if absent, `spotify_album_url` is not emitted.
- HTTPS gate currently enforced via `ensure_https(cfg.wordpress_base_url)` in CLI apply path.Probe response is fetched over the configured URL too — if probe host is plain HTTP, the probe itself does not raise, but `apply` mode rejects before probe runs.

---

## Self-review

- All 12 derived fields covered above spec list (18 — 6 user-precision fields that stay preserved by spec rule "do not overwrite trusted existing values unless explicit normalization rule says same semantic value, wrong shape").
- Field meanings now match spec intent: `music_release_date` = album release on platforms (per Last.fm), `music_listened_at` = post publish date.
- Confidence gating implements spec rule cleanly: report-only path produces no auth header on wire under apply.
- Plan rewritable without losing continuity — old plan left untouched for git history.

## Execution handoff

Plan saved `docs/superpowers/plans/2026-06-19-wordpress-metadata-backfill-derived-fields.md`. Two execution options:

1. **Subagent-Driven** — dispatch fresh subagent per task, review between tasks
2. **Inline** — execute in-session with checkpoints

**Which approach?**
