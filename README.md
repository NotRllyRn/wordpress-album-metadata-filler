# Wordpress-PostToAlbum-Script

One-time Python CLI for backfilling existing WordPress release posts with normalized custom metadata used by archive/search UI.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

## Environment

```bash
cp example.env .env
```
and modify.

Required in `.env` or the shell:

```env
WORDPRESS_BASE_URL=https://your-site.example
LASTFM_API_KEY=your-lastfm-key
```

Required only for writes:

```env
WORDPRESS_USERNAME=api-user
WORDPRESS_APP_PASSWORD=application-password
```

`--apply` refuses to run against `http://` (HTTPS-only credential transport).

## Derived fields

The script emits the following ACF fields when missing or shape-different on the existing post.

| Field | Source | Notes |
|---|---|---|
| `music_total_tracks` | `source.tracks` count | always |
| `music_length_ms` | positive durations | omits when no positive duration (no false 0) |
| `music_avg_track_ms` | avg of positive durations | omits when no positive duration |
| `music_explicit` | any `track.explicit` | only when true |
| `music_release_date` | Last.fm `album.releasedate` | YYYYMMDD from ISO / `15 Jan 2020` / year-only |
| `music_listened_at` | `source.published_at` (post `date_gmt` fallback `date`) | listen-event date |
| `music_match_confidence` | Last.fm match (`high`/`medium`/`low`) | every non-low post |
| `lastfm_release_id` | Last.fm `album.mbid` | non-empty |
| `lastfm_album_url` | `https://www.last.fm/music/{artist}/{album}` | URL-encoded; only when title contains ` - ` |
| `spotify_album_url` | `https://open.spotify.com/album/{source.acf.spotify_album_id}` | only when id present |
| `music_mood_tags` + `genre` taxonomy | Last.fm tags slugified | only when tags non-empty |
| `music_tracks` | normalized rows | only when raw shape differs |
| `unreleased` | `source.acf.unreleased` parsed bool | always (preserved/normalized) |

User-entered fields are preserved untouched (`music_rating`, `music_favorite`, `spotify_album_id`, `music_label`, `music_notes`, `music_source`, `music_review_status`, `previous-listen-posts`).

## Confidence gate

When Last.fm match confidence is `low`, the post is reported as `reasons=low-confidence-match` and no ACF/taxonomy writes are attempted for that post — even fields that are deterministically derivable from the source (track stats, listened_at). Re-runs of a `high` match on a fully-canonical post emit zero diff (`already-normalized`).

Dry-run reason codes: `already-normalized`, `field-diff`, `low-confidence-match`.

## Dry Run

```bash
.venv/bin/python -m post_to_album.cli --batch-size 20 --limit 10
```

## Apply

```bash
.venv/bin/python -m post_to_album.cli --apply --batch-size 20
```

Apply requires `--apply` and WordPress application-password credentials. Refuses plain HTTP.

## Tests

```bash
.venv/bin/python -m pytest -v
```

49 tests; covers normalization, Last.fm parse (incl. release_date variants + Spotify/Last.fm URL helpers), diff semantics (no-op + writeback + missing-source), end-to-end mocked POST request shape, dry-run message keys, and the low-confidence skip path.

## Knowledge graph

`graphify-out/graph.html` (or `GRAPH_REPORT.md`) shows the 237-node / 462-edge codebase map. Rebuild with:

```bash
graphify update .
```
