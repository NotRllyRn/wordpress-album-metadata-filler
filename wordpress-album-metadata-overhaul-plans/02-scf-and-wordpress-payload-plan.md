# Plan 2: Updated SCF Fields and WordPress Payload

## Goal

Define the approved SCF and WordPress payload contract and stop writing fields that no longer exist.

The deployed July 23 export is committed as `scf-export-2026-07-23.json` and was audited against this contract. Its active field names/types, seven repeater children, `d/m/Y` date formats, SCF REST exposure, and the three taxonomy registrations/default effective REST slugs are compatible.

The approved active metadata fields are:

```text
spotify_title
music_rating
music_release_date
music_favorite
music_listened_at
music_notes
music_tracks
music_length_ms
music_avg_track_ms
music_explicit
music_total_tracks
listen_count
spotify_album_id
spotify_album_url
lastfm_release_id
```

The approved active custom taxonomies are:

```text
artist
genre
release_type
```

The implementation contract excludes:

```text
music_mood_tags
unreleased
listen-count
```

## Source-of-truth policy

### SCF metadata

Store attributes that belong to one Spotify release:

- Spotify's raw canonical album title.
- Spotify identifiers and URL.
- Release date.
- Track rows.
- Total and average duration.
- Total tracks.
- Album-level explicit flag.
- Listen count.
- Last.fm-returned MusicBrainz ID when available.

### Taxonomies

Store reusable relationships and classifications:

- `artist`.
- `genre`.
- `release_type`.

Do not add duplicate SCF fields for artist, genre, or release type.

## Exact field-map change

Replace the current auto-fill list with:

```python
AUTO_FILLABLE_FIELDS = (
    "spotify_title",
    "music_tracks",
    "music_length_ms",
    "spotify_album_id",
    "spotify_album_url",
    "music_release_date",
    "music_listened_at",
    "lastfm_release_id",
    "music_total_tracks",
    "music_avg_track_ms",
    "music_explicit",
    "listen_count",
)
```

### Why user-owned fields are excluded

Do not auto-fill or use these fields as required completion criteria:

```text
music_rating
music_favorite
music_notes
```

They are editorial data, not provider metadata. Treat empty values as valid.

### Why `music_tracks` stays included

The program owns the imported track structure. A missing track repeater means the provider import is incomplete.

## Add `spotify_title`

Immediately after fetching the full Spotify album object:

```python
_set_if_empty(acf_patch, existing_acf, "spotify_title", album["name"])
```

Requirements:

- Use `album["name"]` exactly as Spotify returns it.
- Do not lowercase it.
- Do not strip diacritics.
- Do not remove punctuation.
- Do not remove `Deluxe`, `Remastered`, `Anniversary`, `Single`, `EP`, `Version`, `Edit`, `Explicit`, or `Clean`.
- Do not replace the WordPress post title.

Example:

```json
{
  "spotify_title": "The Album (Deluxe Edition)"
}
```

Not:

```json
{
  "spotify_title": "the album"
}
```

## Rename `listen-count` to `listen_count`

Change:

```python
_set_if_empty(acf_patch, existing_acf, "listen-count", 1)
```

To:

```python
_set_if_empty(acf_patch, existing_acf, "listen_count", 1)
```

Also change every field-name reference in:

- `AUTO_FILLABLE_FIELDS`.
- Statistics.
- Logs.
- README examples.
- `plan.md`.
- `questions.md` if the field name is shown.
- `scf-export-field-meanings.md`.
- Planned JSON examples.

Do not support both names in new writes. That would hide configuration errors and create two sources of truth.

## Remove `music_mood_tags`

### Delete payload construction

Remove code that builds or writes:

```python
acf_patch["music_mood_tags"] = ...
```

Remove it from `AUTO_FILLABLE_FIELDS`.

### Keep Last.fm tag extraction for genres

Continue:

```text
selected Last.fm album
        |
        v
filtered top tags
        |
        v
genre taxonomy terms
```

Do not mirror the same tag strings into SCF.

### Rename variables for clarity

Current names such as `mood_tags` should become `genre_names` or `lastfm_tags`.

Example:

```python
genre_names = pick_top_tags(lastfm_info, TAG_BLOCKLIST, limit=3)
```

Not:

```python
mood_tags = pick_top_tags(...)
```

The variable name should match what the data now represents.

## Remove obsolete `unreleased` handling

The approved implementation contract has no `unreleased` field.

Delete it from special boolean handling such as:

```python
if field in ("music_explicit", "unreleased"):
```

Target:

```python
if field == "music_explicit":
```

Search the entire repository for:

```text
unreleased
music_mood_tags
listen-count
```

After implementation, those strings should appear only in migration notes explaining that they were removed, not in executable code or current field documentation.

## Preserve the track repeater contract

Continue writing Spotify track values without title normalization:

```python
{
    "title": track["name"],
    "highlight": existing_or_default,
    "disc_number": track["disc_number"],
    "track_number": track["track_number"],
    "duration_ms": track["duration_ms"],
    "explicit": track["explicit"],
    "spotify_id": track["id"],
}
```

### Preserve editor-owned `highlight` values

If the current implementation rebuilds the full repeater, retain existing highlights by Spotify track ID before replacing rows:

```python
highlights = {r.get("spotify_id"): bool(r.get("highlight")) for r in existing_tracks}
```

Then:

```python
"highlight": highlights.get(track["id"], False)
```

If current code already does this, keep it unchanged. Do not add a more complicated track merge.

## Genre taxonomy policy

### Input

Use filtered Last.fm top tags from the validated Last.fm album only.

### Output

Store up to three accepted names in the `genre` taxonomy.

### Filtering fixes worth including

The current blocklist is case-sensitive. Make it case-insensitive with one change:

```python
re.compile(pattern, re.IGNORECASE)
```

Also reject tags equal to any album artist using `match_key`:

```python
artist_keys = {match_key(a["name"]) for a in album.get("artists", [])}
genre_names = [name for name in genre_names if match_key(name) not in artist_keys]
```

Keep the filter small. Do not attempt to classify every Last.fm tag into a formal genre ontology in this overhaul.

### No-tag behavior

If Last.fm returns no acceptable tags:

- Leave `genre` unchanged when preserving existing nonempty taxonomies.
- Do not write an empty genre array; clearing is outside this overhaul.
- Add a diagnostic to the plan and log.
- Do not create a placeholder genre such as `Unknown`.

## Artist taxonomy policy

Continue deriving artist taxonomy names from the post's existing standard WordPress tags unless a separate migration changes that policy.

Use the original names, not comparison keys:

```python
artist_names = tag_names
```

The comparison normalization introduced for matching must never become a taxonomy label.

### Important behavior to document

A REST update with an `artist` array sets the post's artist term assignments to that array. Therefore the planned JSON must show the complete intended artist name list, not an invisible incremental change.

## Release-type taxonomy policy

Continue mapping Spotify `album_type` to the project's release taxonomy and category behavior.

Keep one explicit mapping table, for example:

```python
RELEASE_TYPE_NAMES = {
    "album": "Album",
    "single": "Single",
    "compilation": "Compilation",
}
```

If existing code distinguishes EPs by another Spotify attribute or name convention, preserve the existing tested rule. Do not infer EP solely by stripping `- EP` from a title. The title should remain raw.

## Completion and skip behavior

The current early return checks only auto-fillable SCF values. That can skip a fully populated post whose `artist` or `release_type` taxonomy is missing.

Use a minimal completion function:

```python
def post_is_complete(post: dict) -> bool:
    acf = post.get("acf") or {}
    metadata_complete = all(is_field_present(acf.get(name), name) for name in AUTO_FILLABLE_FIELDS)
    required_taxonomies = bool(post.get("artist")) and bool(post.get("release_type"))
    return metadata_complete and required_taxonomies
```

Do not require `genre` for completion because Last.fm may legitimately return no tags. Requiring it would repeatedly refetch releases that have no usable Last.fm genre data.

This is a pragmatic completion rule, not a claim that genre is unimportant.

## Non-overwrite and taxonomy fill behavior

Keep the fill-only policy unless a future explicit overwrite operation is approved.

For a planned write:

- Omit every existing nonempty SCF key from `write.acf`; do not represent absence with empty-string, null, empty-list, numeric-zero, or false defaults. A computed zero/false is written only when the destination is empty and the value is semantically correct.
- Existing nonempty `spotify_title` and track data remain untouched.
- Omit `artist` or `genre` from `write.taxonomies` when that taxonomy is already nonempty.
- When filling an empty `artist` or `genre`, include the complete desired name list because the REST array replaces all assignments.
- Never emit an empty taxonomy list merely because no provider values were found.
- `release_type` must result in exactly one computed term, replacing an incorrect or missing release-type assignment as needed.
- A newly generated plan must make all replacement semantics visible.

Do not silently change from fill-empty to overwrite-all as part of this overhaul.

## Category preservation contract

A WordPress `categories` array replaces the post's category assignments. Build it as follows:

1. Copy all current category IDs.
2. Remove only legacy release-type IDs `5`, `6`, `7`, and `98`.
3. Preserve every unrelated and marker category, explicitly including `93` (Relisten) and `200` (Unreleased).
4. Add exactly one legacy category corresponding to the computed release type: Single `5`, Album `6`, EP `7`, or Compilation `98`.

The resulting array contains one computed release-type category plus all preserved categories. Never use `[computed_id]` as the whole category payload.

## `lastfm_release_id` naming caveat

The current program stores `album.getInfo`'s `mbid` in `lastfm_release_id`. That value is a MusicBrainz identifier returned through Last.fm, not a native Last.fm numeric album ID.

For YAGNI:

- Keep the existing SCF field name for now.
- Document its actual meaning.
- Do not introduce a schema migration solely to rename it unless the site needs separate Last.fm and MusicBrainz identifiers.

## Example target ACF payload

```json
{
  "spotify_title": "Heartbreak City",
  "music_release_date": "14/03/2015",
  "music_listened_at": "14/03/2015",
  "music_tracks": [
    {
      "title": "Living for Love",
      "highlight": false,
      "disc_number": 1,
      "track_number": 1,
      "duration_ms": 218000,
      "explicit": false,
      "spotify_id": "spotify-track-id"
    }
  ],
  "music_length_ms": 3330000,
  "music_avg_track_ms": 222000,
  "music_explicit": true,
  "music_total_tracks": 15,
  "listen_count": 1,
  "spotify_album_id": "spotify-album-id",
  "spotify_album_url": "https://open.spotify.com/album/spotify-album-id",
  "lastfm_release_id": "musicbrainz-release-or-release-group-id"
}
```

No `music_mood_tags` key should be present.

## Exact code changes

### Add

- `spotify_title` to the auto-filled field map.
- Raw Spotify title write.
- Optional artist-name filtering for Last.fm genre tags.
- Metadata-plus-required-taxonomy completion check.

### Change

- `listen-count` to `listen_count`.
- Mood-oriented variable names to genre-oriented names.
- Tag regexes to case-insensitive matching.
- Documentation to match the July 23 schema.

### Remove

- `music_mood_tags` from constants, payloads, statistics, and documentation.
- `unreleased` from field-presence logic and documentation.
- Any code that stores normalized Spotify title text.

## Verification checklist

- `spotify_title` exactly equals the Spotify API album `name` string.
- A title containing `Deluxe Edition` retains that text.
- A title containing accents retains them.
- A title containing `- Single` retains it.
- No outgoing ACF payload contains `music_mood_tags`.
- No outgoing ACF payload contains `unreleased`.
- Outgoing payload uses `listen_count`, not `listen-count`.
- Last.fm tags still populate the `genre` taxonomy.
- Artist and release type remain taxonomies only.
- User-owned rating, favorite, notes, and track highlights are not reset.

## Schema source and rollout condition

`scf-export-2026-07-23.json` is the deployed schema evidence. The automated schema contract test records its compatibility; rollout still requires manual one-post verification of category IDs and SCF's `default_to_current_date` behavior.
