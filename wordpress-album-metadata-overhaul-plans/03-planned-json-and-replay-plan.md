# Plan 3: Complete `planned.json` and Offline WordPress Replay

## Goal

Make the planned output both:

1. A complete review artifact showing everything the program intends to write.
2. A self-contained local input that can later be applied to WordPress without rerunning Spotify or Last.fm lookups and without refetching source posts or source post tags.

The replay phase may still contact WordPress to:

- Resolve or create taxonomy terms.
- Update each target post.

Those calls are necessary writes and destination-side identity resolution, not provider refetches.

## Problem with the current format

Current entries are sparse REST fragments such as:

```json
{
  "post_id": 2880,
  "title": "Heartbreak City",
  "body": {
    "acf": {
      "listen-count": 1
    },
    "categories": [6],
    "release_type": [565]
  }
}
```

Limitations:

- It may show only one missing field rather than the complete intended post update.
- Taxonomy IDs are unreadable without another lookup.
- It cannot safely represent missing terms during a true no-write dry run.
- It has no schema version.
- It has no generation timestamp.
- It has no Spotify or Last.fm match evidence for review.
- The existing CLI does not consume it.

## Important dry-run correction

The current planning path calls `_ensure_term`, which may create WordPress taxonomy terms even when the run is described as a dry run.

That behavior must be removed.

A planning command should not create terms or update posts. Store taxonomy names in the plan and resolve them only in `apply-plan`.

This also makes plans portable across WordPress environments where term IDs differ.

## Recommended plan schema

Use one versioned JSON object rather than a bare array:

```json
{
  "schema_version": 1,
  "generated_at": "2026-07-23T01:30:00Z",
  "patches": [
    {
      "post_id": 2880,
      "post_title": "Heartbreak City",
      "source_modified": "2026-07-22T18:20:00Z",
      "matches": {
        "spotify": {
          "id": "spotify-album-id",
          "title": "Heartbreak City",
          "artists": ["Example Artist"],
          "score": 1.0
        },
        "lastfm": {
          "title": "Heartbreak City",
          "artist": "Example Artist",
          "mbid": "musicbrainz-id",
          "score": 1.0,
          "track_overlap": 1.0
        }
      },
      "write": {
        "acf": {
          "spotify_title": "Heartbreak City",
          "music_release_date": "01/01/2026",
          "music_listened_at": "01/01/2026",
          "music_tracks": [
            {
              "title": "Example Track",
              "highlight": false,
              "disc_number": 1,
              "track_number": 1,
              "duration_ms": 218000,
              "explicit": false,
              "spotify_id": "spotify-track-id"
            }
          ],
          "music_length_ms": 218000,
          "music_avg_track_ms": 218000,
          "music_explicit": false,
          "music_total_tracks": 1,
          "listen_count": 1,
          "spotify_album_id": "spotify-album-id",
          "spotify_album_url": "https://open.spotify.com/album/spotify-album-id",
          "lastfm_release_id": "musicbrainz-id"
        },
        "categories": [93, 6],
        "taxonomies": {
          "artist": ["Example Artist"],
          "genre": ["rock", "alternative"],
          "release_type": ["Album"]
        }
      },
      "diagnostics": []
    }
  ]
}
```

## Why use taxonomy names in the plan

WordPress post updates require term IDs, but names are better plan data because they are:

- Human-readable.
- Stable across environments.
- Sufficient to find or create terms during application.
- Compatible with a true no-write planning stage.
- Free from duplicated `{name, id}` structures.

Do not save both a human taxonomy list and a second ID taxonomy list as two sources of truth.

At apply time, create the REST body only from keys present in `write`. Never use `.get(..., [])` in a way that turns an absent write key into an empty replacement array. Copy `acf` or `categories` only when present, and materialize only present, nonempty taxonomy name lists.

This is the only materialization step needed.

## What `write.acf` should contain

It should contain every SCF value the current plan intends to send for that post, including the complete track repeater when tracks will be written.

It should not contain:

- Existing nonempty fields that the fill-empty policy will not touch.
- Editorial fields such as `music_rating`, `music_favorite`, or `music_notes` unless a future explicit feature owns them.
- Removed fields.
- Diagnostic scores.

Therefore `write` means exactly "this plan intends to write these values," not "all data ever known about the release."

## What `matches` should contain

Keep only enough data to review the two provider decisions:

### Spotify

- ID.
- Raw canonical title.
- Raw artist list.
- Final score.

### Last.fm

- Selected raw title.
- Selected raw artist.
- MBID when present.
- Final title-and-artist score.
- Optional track overlap when Last.fm supplied tracks.

Do not copy entire provider responses into the plan. That would make files large, unstable, and harder to review.

## What `diagnostics` should contain

Use short machine-readable strings or small objects, for example:

```json
[
  {
    "code": "lastfm_no_tags",
    "message": "Validated Last.fm album returned no acceptable genre tags."
  }
]
```

Supported codes:

```text
spotify_no_results
spotify_low_confidence
spotify_ambiguous
spotify_provider_error
lastfm_no_results
lastfm_low_confidence
lastfm_ambiguous
lastfm_provider_error
lastfm_no_mbid
lastfm_no_tracks
lastfm_track_mismatch
lastfm_no_tags
```

`spotify_provider_error` and `lastfm_provider_error` cover network, HTTP, authentication, rate-limit, provider API, and malformed-response failures. Their messages may include concise sanitized details, but must not include credentials. They are never aliases for the corresponding `*_no_results` code.

Accepted patches may have warnings such as `lastfm_no_mbid`. Rejected posts belong in `out/unresolved.json`, not in `patches`. Use the minimal versioned shape:

```json
{
  "schema_version": 1,
  "unresolved": [
    {
      "post_id": 2880,
      "post_title": "Heartbreak City",
      "diagnostics": [
        {
          "code": "spotify_provider_error",
          "message": "Spotify request timed out."
        }
      ]
    }
  ]
}
```

Validate this file with the same strict rules used below: exact supported keys, a unique positive `post_id`, a string `post_title`, and a nonempty diagnostics list containing only supported `{code, message}` objects. This preserves provider errors distinctly without duplicating a separate error mechanism.

## New CLI command

Add a separate subcommand:

```bash
python post_to_album.py apply-plan out/planned.json
```

Optional existing-style batching arguments:

```bash
python post_to_album.py apply-plan out/planned.json --offset 0 --limit 10
```

Do not overload `run` with an ambiguous `--from-file` mode. A separate verb makes destructive intent obvious.

## `apply-plan` behavior

### Step 1: Load and validate the file

```python
plan = json.loads(Path(args.plan).read_text())
if plan.get("schema_version") != 1:
    raise SystemExit("Unsupported plan schema")
patches = plan.get("patches")
if not isinstance(patches, list):
    raise SystemExit("Invalid plan: patches must be a list")
```

Keep validation dependency-free and explicit. Do not add JSON Schema as a dependency.

### Step 2: Validate the complete plan before slicing or writes

Validation is recursive and rejects booleans where integers are required. Require:

```text
root: object with exactly supported keys
schema_version: integer exactly 1
generated_at: nonempty string
patches: list
patch.post_id: unique positive integer
patch.post_title: string
patch.source_modified: string or null when present
patch.matches: object with only supported provider evidence
patch.write: nonempty object with only acf/categories/taxonomies
patch.write.acf: object when present; only approved Plan 02 keys and valid value shapes
patch.write.categories: nonempty list of unique positive integers when present
patch.write.taxonomies: object when present; only artist/genre/release_type
taxonomy values: nonempty lists of nonempty strings, deduplicated by match_key
release_type value: exactly one approved name when present
diagnostics: list of supported `{code, message}` objects from the code list above
```

Reject unknown keys, wrong nesting, empty replacement arrays, duplicate post IDs, non-finite numbers, and removed SCF fields. Validate every patch in the file before term creation or post updates. Only after full validation may `offset`/`limit` select patches; an invalid out-of-slice patch still blocks all writes. Resolve taxonomy terms only for the validated slice.

### Step 3: Slice, then collect unique taxonomy names

After `validate_plan(plan)` has validated every patch:

```python
selected_patches = slice_items(plan["patches"], args.offset, args.limit)
wanted = {
    taxonomy: {
        name
        for patch in selected_patches
        for name in patch["write"].get("taxonomies", {}).get(taxonomy, [])
    }
    for taxonomy in ("artist", "genre", "release_type")
}
```

`slice_items` validates `offset >= 0` and `limit is None or limit >= 0` before returning the standard Python slice.

### Step 4: Resolve each taxonomy once

For each taxonomy:

1. Fetch all existing terms page by page (`per_page=100`), following `X-WP-TotalPages` when present; otherwise stop only on a short/empty page. A full page is not proof that pagination is complete.
2. Build a `match_key(name) -> id` cache.
3. Create only missing names.
4. Add created terms to the cache.

Handle an out-of-range page response as normal pagination completion only when prior pages were valid; other HTTP/provider errors remain failures.

This avoids one lookup per post.

### Step 5: Materialize and POST each body

```python
for patch in selected_patches:
    body = materialize_body(patch["write"], term_ids)
    wp.update_post(patch["post_id"], body)
```

No call should be made to:

- `wp.list_posts()`.
- `wp.list_tags()`.
- Spotify authentication.
- Spotify search.
- Spotify album endpoints.
- Last.fm search.
- Last.fm getInfo.

### Step 6: Write an application result

Create a small result file, for example:

```text
out/applied.json
```

Suggested shape:

```json
{
  "schema_version": 1,
  "plan": "out/planned.json",
  "applied_at": "2026-07-23T02:00:00Z",
  "succeeded": [2880],
  "failed": []
}
```

This is operational evidence, not another cache format. Keep failure messages only for failed posts.

## Environment-variable behavior

The current loader warns about all provider credentials for every command. Change environment validation by command.

### `run` requires

```text
WORDPRESS_BASE_URL
WORDPRESS_USERNAME
WORDPRESS_APP_PASSWORD
SPOTIFY_CLIENT_ID
SPOTIFY_CLIENT_SECRET
LASTFM_API_KEY
```

### `apply-plan` requires only

```text
WORDPRESS_BASE_URL
WORDPRESS_USERNAME
WORDPRESS_APP_PASSWORD
```

### `fuzzy` requires

```text
SPOTIFY_CLIENT_ID
SPOTIFY_CLIENT_SECRET
```

The command performs Spotify search and therefore cannot be credential-free.

### `stats` requires only WordPress credentials

```text
WORDPRESS_BASE_URL
WORDPRESS_USERNAME
WORDPRESS_APP_PASSWORD
```

Implement with one small helper:

```python
def require_env(*names):
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")
```

## Plan generation behavior

### Default output

Use:

```text
out/planned.json
```

Replace references to `planned_patches.json` in current documentation and code.

### Always serialize the same in-memory patch object

Both modes should use the same plan structure:

- Dry run atomically writes versioned `out/planned.json` and versioned `out/unresolved.json`.
- `run --apply`, if retained for backward compatibility, may apply the same generated in-memory plan after writing it.
- `apply-plan` loads that exact structure from disk.

Do not maintain one body builder for immediate writes and another for saved plans.

### Atomic file write

Use a temporary file and rename:

```python
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
tmp.replace(path)
```

This prevents a terminated run from leaving partially written JSON.

## Stale-plan behavior

The user explicitly wants replay without refetching posts. That means `apply-plan` will not know whether somebody edited a post after plan generation.

Document this clearly:

- Review the plan before applying.
- Apply it soon after generation.
- Regenerate it after significant WordPress edits.
- `source_modified` is audit information only in the first implementation.

Do not add optimistic-concurrency refetching in this feature because it conflicts with the no-refetch requirement and is not needed for the initial workflow.

## Taxonomy replacement semantics

A taxonomy array in a WordPress post update represents the complete desired assignment list for that taxonomy.

Therefore:

- Omit `artist` or `genre` when it is already nonempty.
- When filling an empty `artist` or `genre`, include the complete desired names.
- `write.taxonomies.release_type` contains exactly one computed term.
- Never include an empty taxonomy list; clearing is outside this plan.

Categories have the same replacement risk: `write.categories` must preserve every current unrelated/marker ID, including `93` and `200`, remove only legacy release-type IDs `5`, `6`, `7`, and `98`, then add exactly one computed release-type ID. Omit `categories` only if the resulting assignment already matches.

## Examples

### Complete release plan

```json
{
  "post_id": 2880,
  "post_title": "Heartbreak City",
  "matches": {
    "spotify": {
      "id": "abc",
      "title": "Heartbreak City",
      "artists": ["Artist A"],
      "score": 1.0
    },
    "lastfm": {
      "title": "Heartbreak City",
      "artist": "Artist A",
      "mbid": "mbid-value",
      "score": 1.0,
      "track_overlap": 0.93
    }
  },
  "write": {
    "acf": {
      "spotify_title": "Heartbreak City",
      "listen_count": 1,
      "spotify_album_id": "abc"
    },
    "categories": [93, 6],
    "taxonomies": {
      "artist": ["Artist A"],
      "genre": ["pop", "dance"],
      "release_type": ["Album"]
    }
  },
  "diagnostics": []
}
```

### No Last.fm genres

```json
{
  "write": {
    "acf": {
      "spotify_title": "New Release",
      "spotify_album_id": "xyz"
    },
    "categories": [200, 6],
    "taxonomies": {
      "artist": ["Artist A"],
      "release_type": ["Album"]
    }
  },
  "diagnostics": [
    {
      "code": "lastfm_no_tags",
      "message": "No acceptable genre tags were returned."
    }
  ]
}
```

The omitted `genre` key means "do not change genres," not "clear genres."

### Existing metadata only needs one field

Under fill-empty behavior, a plan may still legitimately contain only:

```json
{
  "acf": {
    "spotify_title": "Canonical Spotify Title"
  }
}
```

That is complete because it shows every value that this run intends to write, not every value already stored on WordPress.

## Exact code changes

### Add

- `PLAN_SCHEMA_VERSION = 1`.
- `build_plan()`.
- `write_plan_atomic()`.
- `validate_plan()`.
- `materialize_body()`.
- `cmd_apply_plan()`.
- CLI `apply-plan` parser.
- Command-specific environment validation.
- Optional `out/applied.json` result.

### Change

- Planned entry key `title` to `post_title` for clarity.
- `body` to `write` because taxonomy names are not yet a raw WordPress REST body.
- Taxonomy planning from IDs to names.
- Output path to `out/planned.json`.
- Immediate apply to reuse the plan materialization path.

### Remove

- Taxonomy term creation during dry-run planning.
- Automatic Spotify and Last.fm client creation in `apply-plan`.
- Source post and source tag fetches in `apply-plan`.
- The undocumented assumption that planned output is display-only.

## Verification checklist

- A dry run makes no WordPress writes, including no taxonomy-term creation.
- `out/planned.json` contains `schema_version`, `generated_at`, and `patches`.
- Each patch shows raw Spotify title and artist match evidence.
- Each patch shows every ACF field that will be written.
- Each patch shows taxonomy names rather than opaque IDs.
- Track rows are fully present when `music_tracks` will be written.
- `apply-plan` succeeds with Spotify and Last.fm credentials unset.
- `apply-plan` does not list posts or tags.
- Missing taxonomy terms are created once and reused.
- Applied REST bodies contain integer taxonomy IDs.
- Invalid plans fail before the first WordPress update.
- A failed post is reported without hiding successful post IDs.

## Official behavior relied upon

WordPress updates a post with `POST /wp/v2/posts/<id>`. Category and registered REST taxonomy assignments are represented by term-ID arrays in the post body. This contract requires the custom SCF taxonomies to have `show_in_rest` enabled; verify that requirement against the real deployed export before rollout.
