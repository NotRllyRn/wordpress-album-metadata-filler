# WordPress Post → Album CLI

A dependency-free Python 3.10+ CLI that plans fill-only SCF metadata and taxonomy updates for WordPress album posts using Spotify and Last.fm. It is single-threaded and treats one post as one release: the rendered post title is the album query and post tags supply artist names.

## Current matching behavior

Query values are HTML-unescaped and trimmed, but otherwise raw: accents, punctuation, suffixes, and text such as `Deluxe` or `Remastered` are retained. Case folding, whitespace folding, and Unicode normalization are used only for comparisons, never for queries or stored values.

Spotify uses a strongest-intent-first search ladder, deduplicates candidates by ID, and requires title, artist, and combined-score gates plus a safe gap from a second qualifying candidate. It then fetches the full album and all paginated tracks. `spotify_title` is the exact title from that full Spotify album object.

Last.fm matching is `album.search` → select one safe candidate → `album.getInfo`. A usable candidate MBID is preferred; otherwise the selected raw artist/title are sent with `autocorrect=0`. The returned identity (and tracks when supplied) is validated before its MBID or tags are used. Missing MBIDs are omitted and diagnosed rather than written as empty strings. Filtered Last.fm tags feed only the `genre` taxonomy.

## Fill-only write contract

The CLI writes an active SCF key only when that key is empty:

- `spotify_title`
- `music_tracks` (`disc_number`, `track_number`, `title`, `duration_ms`, `spotify_id`, `highlight`, `explicit`)
- `music_length_ms`, `music_avg_track_ms`, `music_total_tracks`, `music_explicit`
- `spotify_album_id`, `spotify_album_url`
- `music_release_date`, `music_listened_at`
- `lastfm_release_id`
- `listen_count`

`music_rating`, `music_favorite`, and `music_notes` are editor-owned. The removed `music_mood_tags`, `unreleased`, and hyphenated `listen-count` keys are not written. Existing nonempty ACF values are omitted from the plan.

Taxonomies are also fill-only: existing nonempty `artist` and `genre` assignments are omitted; empty ones receive complete desired name lists. `release_type` always contains exactly one computed `Album`, `EP`, `Single`, or `Compilation` term. Category replacement removes only legacy release-type IDs 5/6/7/98, adds the computed twin, and preserves marker IDs (including 93 and 200) and all unrelated IDs.

## Configuration

Copy `example.env` to `.env`. Requirements are command-specific:

| Command | Required variables |
| --- | --- |
| `run` | `WORDPRESS_BASE_URL`, `WORDPRESS_USERNAME`, `WORDPRESS_APP_PASSWORD`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `LASTFM_API_KEY` |
| `apply-plan`, `stats` | `WORDPRESS_BASE_URL`, `WORDPRESS_USERNAME`, `WORDPRESS_APP_PASSWORD` |
| `fuzzy` | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` |

The CLI loads `.env` by default; use `--env PATH` to select another file.

## Commands and safe workflow

```bash
# Inspect current fill rates.
python post_to_album.py stats

# Diagnose Spotify matching only.
python post_to_album.py fuzzy "Été (Deluxe Edition)" "Beyoncé"

# Plan a batch. No post updates or taxonomy creation occur.
python post_to_album.py run --limit 10

# Review both versioned artifacts.
less out/planned.json
less out/unresolved.json

# Apply the reviewed artifact without Spotify/Last.fm or source-post refetches.
python post_to_album.py apply-plan out/planned.json --limit 10
```

`run` defaults to true no-write planning. Planning still reads WordPress, Spotify, and Last.fm, but does not create terms or update posts. It atomically writes versioned `out/planned.json` and `out/unresolved.json`. The plan stores provider evidence, only intended writes, and taxonomy names—not environment-specific IDs.

`apply-plan` strictly validates the complete file before slicing or any write, resolves/creates destination taxonomy terms, converts names to integer IDs, updates posts, and atomically writes `out/applied.json`. Review plans and apply them soon after generation; `source_modified` is audit information, not a concurrency check. `run --apply` remains only as a deprecated compatibility path; prefer review followed by `apply-plan`.

Common batching flags are `--offset N`, `--limit N`, and `--out-dir DIR`. Run `python post_to_album.py COMMAND --help` for command details.

## Safety and scope

- Automated tests use fakes and make no live writes.
- Planning never resolves or creates WordPress terms.
- Existing nonempty provider and editor values are never overwritten.
- Media, featured images, ratings, favorites, and notes are untouched.
- `out/planned_patches.json` is protected historical output and is neither the current plan format nor an input to `apply-plan`.
- Application has no programmatic rollback; retain the reviewed plan and `applied.json` as evidence.

## Documentation and rollout status

The canonical design sequence is:

- [`Plan 00: index`](wordpress-album-metadata-overhaul-plans/00-overhaul-index.md)
- [`Plan 01: search and matching`](wordpress-album-metadata-overhaul-plans/01-search-and-matching-plan.md)
- [`Plan 02: SCF and WordPress payload`](wordpress-album-metadata-overhaul-plans/02-scf-and-wordpress-payload-plan.md)
- [`Plan 03: planned JSON and replay`](wordpress-album-metadata-overhaul-plans/03-planned-json-and-replay-plan.md)
- [`Plan 04: integration, tests, and rollout`](wordpress-album-metadata-overhaul-plans/04-implementation-order-and-tests.md)

The root `plan.md`, `questions.md`, and `vision.md` are historical context, not the current contract. `scf-export-2026-07-05.json` is also historical. The real deployed July 23 SCF export is missing from this repository; live rollout remains blocked until it is obtained and compared with Plan 02's fields, types, repeater children, taxonomy REST bases, and date formats. Do not invent an export.
