# SCF field meanings

> **Schema evidence:** `scf-export-2026-07-23.json` is the deployed export and has been audited as compatible with Plan 02 for active names/types, seven repeater children, `d/m/Y` dates, SCF REST exposure, and taxonomy registrations/default REST slugs. `scf-export-2026-07-05.json` remains historical. Rollout still requires a manual one-post category-ID check and verification of SCF's `default_to_current_date` behavior.

## Current Plan 02 contract

Provider-owned fields are filled only when empty:

- `spotify_title`: exact raw canonical title from the selected full Spotify album object; accents, punctuation, and edition suffixes are preserved.
- `music_tracks`: complete imported track rows. Children are `disc_number`, `track_number`, `title`, `duration_ms`, `spotify_id`, `highlight`, and `explicit`. Re-import preserves an existing highlight by Spotify track ID.
- `music_length_ms`: sum of imported track durations.
- `music_avg_track_ms`: average duration using total tracks.
- `music_total_tracks`: Spotify album total (or imported row count fallback).
- `music_explicit`: true when any imported track is explicit.
- `spotify_album_id`, `spotify_album_url`: selected Spotify album identity and URL.
- `music_release_date`: canonical Spotify release date formatted as calendar-valid `dd/mm/YYYY` for SCF.
- `music_listened_at`: WordPress post date formatted as calendar-valid `dd/mm/YYYY` for SCF.
- `lastfm_release_id`: MusicBrainz ID returned by the validated Last.fm `album.getInfo` result. It is omitted and diagnosed when absent; search-result MBID is used to prefer the getInfo lookup, not blindly stored.
- `listen_count`: defaults to integer `1` when empty.

Editor-owned active fields are never auto-filled: `music_rating`, `music_favorite`, and `music_notes`.

Filtered Last.fm tags populate only the `genre` taxonomy. `artist` and `genre` are fill-only; `release_type` contains exactly one computed release type. The removed `music_mood_tags`, `unreleased`, and `listen-count` fields are not active and must not appear in new writes.

## Historical July 5 notes

The historical export described track rows, rating/favorite fields, duration and Spotify identity, listened/release dates, Last.fm identity, track totals, explicitness, mood tags, unreleased status, and a listen-count index. Those notes explain older data only. In particular, `music_mood_tags`, `unreleased`, and the older listen-count naming were removed or replaced by the current contract above.
