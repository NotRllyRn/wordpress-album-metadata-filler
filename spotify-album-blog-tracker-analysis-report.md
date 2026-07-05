# Notes for Building a CLI That Modifies WordPress Posts

These notes are extracted specifically because the next step is a CLI that hits the same SQLite DB and the same WordPress REST API to retrofit more music metadata into each existing post. Everything below is the surface area that CLI will need.

Files referenced are relative to the repo root. Path names match the live repository.

---

## 1. Spotify release-type heuristics

There are two distinct things the codebase calls "release type". They are different and a CLI must treat them as separate fields.

### 1a. Computed release type (Album / EP / Single / Compilation)

Source of truth: `src/utils.py::compute_release_type(tracks, raw_spotify_type)`.

```python
def compute_release_type(tracks: List[dict], raw_spotify_type: str) -> str:
    if raw_spotify_type.lower() == 'compilation':
        return 'Compilation'

    track_count = len(tracks)
    total_ms = sum(t.get('duration_ms', 0) for t in tracks)
    max_track_ms = max((t.get('duration_ms', 0) for t in tracks), default=0)

    duration_30m = 1800000  # 30 minutes
    duration_10m = 600000   # 10 minutes

    if track_count >= 7 or total_ms >= duration_30m:
        return 'Album'
    elif (4 <= track_count <= 6 and total_ms < duration_30m) or \
         (1 <= track_count <= 3 and max_track_ms >= duration_10m):
        return 'EP'
    elif 1 <= track_count <= 3 and total_ms < duration_30m and max_track_ms < duration_10m:
        return 'Single'
    else:
        return 'Album'  # fallback
```

Rules, in plain English, exactly as the code applies them (priority order is top-down):

1. If Spotify's raw `album_type` is `compilation` (case-insensitive) → `"Compilation"`. Note: this branch fires on **the raw Spotify field**, not on multi-artist detection.
2. Otherwise, compute against the track list:
   - **`Album`** if track count `>= 7` OR total duration `>= 30 min` (1,800,000 ms).
   - **`EP`** if track count `4–6` AND total `< 30 min`, OR track count `1–3` AND longest track `>= 10 min` (600,000 ms).
   - **`Single`** if track count `1–3` AND total `< 30 min` AND longest track `< 10 min`.
   - **`Album`** as fallback (this branch is unreachable from the conditions above, so it is dead, but it is the explicit fall-through).

Edge cases to be aware of:

- The four outputs of this function are stored as the **string** values `"Album"`, `"EP"`, `"Single"`, `"Compilation"` (matching the `ReleaseType` enum in `src/models.py`).
- This function says it "matches plugin logic", meaning a WordPress-side plugin treats the same thresholds as authoritative. If the CLI derives a different value, it will not match what the tracker wrote.
- Track list passed in comes from `Spotify.get_album_tracks()`; it includes local and non-playable tracks. Scoring uses the full raw list, not the filtered "countable" subset.

### 1b. Raw Spotify `album_type` (different field)

Stored separately as `raw_spotify_type`. Comes straight from `album_data["album_type"]`. Spotify only emits three values: `album`, `single`, `compilation`. The tracker uses it earlier in the eligibility filters — see below — independently of the computed type.

### 1c. How the computed type is used downstream

- Categories on WordPress posts: `src/publisher.py::Publisher.publish_release` sets `category_ids = [self.category_cache[release.release_type.value]]`. `_ensure_categories()` pre-creates exactly `["Album", "EP", "Single", "Compilation", "Relisten"]`, and `Relisten` gets appended as a second category when `as_relisten=True`. So the value produced by `compute_release_type` is **also the WordPress category slug the post was filed under**.
- The four-way enum lives in `src/models.py::ReleaseType`. Any code reusing the DB string column must tolerate these exact spellings.
- Stored on `release_lifecycle.release_type` (TEXT column) and on `saved_library_album.release_type`.

### 1d. Other Spotify-derived track-level rules worth knowing

In `src/tracker.py::_qualifies_for_tracking`, an **album was only ever marked as "tracked"** when **every** of these is true at the moment of playback (the raw `album_type == "album"` filter means **only** raw Spotify albums qualify for auto-tracking, regardless of the computed type):

- `item.type == "track"`, item has an `album` object
- `album.album_type == "album"` (raw field)
- Track is not local
- `context.type == "album"` and `context.uri == album.uri` (i.e., playing from the album's own context, not from a playlist/radio)
- Not in shuffle

Track-by-track counts (used for `is_countable`): `not t.is_local AND t.is_playable` (see `_build_release_from_spotify`).

Skip behavior: `ReleaseType.SINGLE` releases are skipped from auto-tracking — `_poll_once` returns early when the computed type is `Single`. So the DB's mostly-tracked set excludes singles unless they were entered manually.

---

## 2. WordPress API surface

### 2a. Connection details and auth

- Source: `src/wordpress_client.py::WordPressClient.__init__` plus `src/config.py`
- Base URL = `WORDPRESS_URL` from env (in deployment: `http://10.17.3.3:8085` — internal, see `config.py:35`).
- Public-facing URL for writer-facing links lives in `WORDPRESS_PUBLIC_URL` (default `https://musicblog.callita.day`).
- API base: `{base_url}/wp-json/wp/v2`.
- Auth: HTTP Basic, precomputed in `__init__` as a base64 of `username:app_password`, sent as the `Authorization` header on every JSON request.

  ```
  Authorization: Basic <base64(user:app_password)>
  Content-Type: application/json
  Accept: application/json
  ```

- Client uses `httpx.AsyncClient` with `timeout=30.0`. Media uploads use a separate `httpx.AsyncClient` with `timeout=60.0` and `Content-Type` stripped.
- Required env vars: `WORDPRESS_URL`, `WORDPRESS_USERNAME`, `WORDPRESS_APP_PASSWORD` (validated in `src/config.py::_validate`).

### 2b. Endpoints the tracker/publisher actually use

From `src/wordpress_client.py`:

| Method | Path                                           | Used for                                                                  |
|--------|------------------------------------------------|---------------------------------------------------------------------------|
| GET    | `/wp/v2/posts`                                 | List posts; `_fields=id,title,tags,link` used for the cache refresh        |
| POST   | `/wp/v2/posts`                                 | Create a new post (`create_post`)                                         |
| POST   | `/wp/v2/posts/{id}`                            | Update existing post (`update_post`) — same path, not PATCH, JSON body    |
| DELETE | `/wp/v2/posts/{id}` (optional `?force=true`)   | Trash by default; force-delete with `force=true` (`delete_post`)          |
| GET    | `/wp/v2/categories?per_page=100`               | List all categories (`get_categories`)                                    |
| POST   | `/wp/v2/categories`                            | Create category (`create_category`)                                       |
| GET    | `/wp/v2/tags?per_page=100&page=N`               | List tags with cache; cached on X-WP-Total + first-page hash              |
| GET    | `/wp/v2/tags/{id}`                             | Lookup tag by ID (`get_tag_by_id`)                                        |
| POST   | `/wp/v2/tags`                                  | Create tag; 400 with `code=term_exists`/`existing_term_slug`/`term_exists_invalid` is **handled** by re-fetching the existing tag |
| POST   | `/wp/v2/media`                                 | Multipart upload (`upload_media`) — uses `file` field, MIME `image/jpeg`, optional `alt_text` form field |
| POST   | `/wp/v2/media/{id}`                            | Update media metadata                                                    |
| DELETE | `/wp/v2/media/{id}` (optional `?force=true`)   | Delete media                                                             |

Pagination conventions:

- Posts: `per_page=100` default, full pagination via `X-WP-TotalPages`, count via `X-WP-Total`.
- Tags: same, fallback scan if `X-WP-TotalPages` is missing — keeps reading pages 2..N until a page comes back with fewer than `per_page` entries OR hard ceiling of 10,000.
- Caches: First-page SHA-256 + `X-WP-Total` short-circuit on both posts and tags. See `WORDPRESS_POSTS_CACHE` keys below.

### 2c. What the publisher puts on a post today

`src/publisher.py::Publisher.publish_release` builds exactly this post body — this is what each existing post already contains (or is missing):

```python
post_data = {
    "title": release.title,
    "content": "",                              # always empty placeholder today
    "status": "publish",
    "categories": [release_type_category_id] +  # one of: Album / EP / Single / Compilation
                  ([relisten_category_id] if as_relisten else []),
    "tags": [artist_tag_ids...],                # one tag per artist name
    "featured_media": cover_artwork_media_id,   # 0 if upload failed
}
```

Important — what is **not** presently in the post:

- `content` is empty (the Discord modal flow overwrites this later via `Publisher.update_post_content`, which formats plain text into `<p>` blocks; see `format_discord_content_for_wordpress`).
- No `excerpt` is set.
- No `slug` is set (WP will auto-generate from title).
- No `meta` fields or custom fields are written.
- Track listing is not embedded.
- `release_date` is not surfaced.
- No duration / track count is shown.
- No Spotify link / URI is embedded.
- No cover alt-text is exposed in the post body (alt-text is only on the media attachment via `media.alt_text`).

### 2d. What the publisher classifies as a "duplicate"

In `src/tracker.py::_check_duplicate`, two releases are duplicates iff:

1. `release.normalized_title == post.normalized_title`, AND
2. The set of normalized release artists equals the set of normalized post artists (post artists come from the cached tag names — see below).

`normalize_text()` (`src/utils.py`) does:

- Unicode `NFKC` normalization
- `casefold()`
- outer whitespace trim
- collapse internal whitespace to a single space
- strip zero-width characters (U+200B–U+200D, FEFF)

`normalize_artist_list()` additionally strips commas before normalization. This is why "Artist A, Artist B" (a joining convention) is treated as a list of two artists.

The post cache stores artists as **the names of the WordPress tags assigned to that post** (`refresh_post_cache` in `publisher.py` builds `post_tags = [tag_map.get(t, "") for t in post.get("tags", [])]`). So the dedup key on the WordPress side is *whatever tags the post currently has* — if a CLI adds/removes tags, it changes the duplicate-detection identity of the post.

### 2e. Post publish-side effects (state the CLI must respect)

After a publish, two side effects land in SQLite (in `release_lifecycle`):

- `wordpress_post_id` populated (matches the WP post id)
- `wordpress_media_id` populated (matches the cover-art media id)

If a release was a relisten, additionally:

- `is_relisten = 1`
- `duplicate_state = "found"`
- `duplicate_post_id` points to the existing post that was replaced

The completion-then-cleanup cycle also means a typical published row gets deleted from `release_lifecycle` after `PUBLISHED_RELEASE_RETENTION` (24h) by `_cleanup_published_releases_if_due`. So **the canonical link between a published post and Spotify metadata is the `wordpress_post_id` column on `saved_library_album`** (and any surviving `release_lifecycle` row), not the live `release_lifecycle` table.

---

## 3. Local SQLite database layout that ties everything together

Database path: `data/album_tracker.db` (from `src/config.py::db_path`).

### 3a. Tables that store music metadata the CLI should reuse

`release_lifecycle` (`migrations/001_initial_schema.sql`, plus `is_relisten` from `002_*`):

- `spotify_id` (TEXT, UNIQUE) — primary join key to Spotify
- `title`, `normalized_title`
- `release_type` (TEXT, one of `Album`/`EP`/`Single`/`Compilation` — computed value)
- `raw_spotify_type` (TEXT — Spotify's raw `album`/`single`/`compilation`)
- `cover_url`
- `release_date` (TEXT, Spotify's `release_date` string — precision varies, often YYYY-MM-DD)
- `total_tracks`, `total_duration_ms`
- `progress`, `status` (LifecycleStatus enum)
- `first_seen`, `last_seen`, `completed_at`, `published_at` (ISO datetime strings)
- `wordpress_post_id`, `wordpress_media_id` — the link to the WP post. **This is the critical join column for the CLI.**
- `duplicate_state` (`"found"` / `"none"`), `duplicate_post_id` (set on relistens)
- `is_relisten` (BOOL, added in migration 002)

`release_artist` (per release): `spotify_id`, `name`, `normalized_name`.

`release_track` (per release): `spotify_id`, `title`, `normalized_title`, `duration_ms`, `disc_number`, `track_number`, `is_countable`, `listened`, `listened_at`, `listened_source`.

`saved_library_album` (`migrations/004_*`) is the wider Spotify library snapshot. Has `spotify_id` PK, `title`, `normalized_title`, `artists_json`, `normalized_artists_json`, `album_type` (raw Spotify), `release_type` (computed, same enum), `cover_url`, `added_at`, `is_posted_listened`, `wordpress_post_id`. **Use this row as the CLI's source of truth for what is already published to WP** — the row stays around even after `release_lifecycle` is purged.

`saved_library_snapshot_item` (`migrations/005_*` is incremental reconciliation only — `spotify_id`, `spotify_uri`, `added_at`, `position`, `last_seen_at`. Probably not interesting for the CLI.

### 3b. Tables that hold WordPress sync state

`wordpress_post_cache` (`migrations/001_*`):

- `id` (PK, the WP post id — primary join key)
- `title`, `normalized_title`
- `artists_json` (JSON list of tag names currently on the post)
- `normalized_artists_json`
- `link` (the WP `link` field)

This table is rebuilt wholesale (`DELETE` + bulk `INSERT`) on every full post-cache refresh — see `Publisher.refresh_post_cache`. It is the only local view of existing posts.

`service_state` (key/value cache, `migrations/001_*`):

- Key `wordpress_post_cache.x_wp_total` — string `X-WP-Total` value from last refresh
- Key `wordpress_post_cache.first_page_hash` — sha256 hex of the last refreshed page 1 body
- Other runtime keys (e.g. `current_playback_state`)

A CLI that wants to detect "did the cache change under us" can compare against these.

`discord_prompt`: stores pending/expired prompts and joins to `release_id` (Spotify id) and `wordpress_post_id`. Not needed for read-only metadata enrichment but may matter if you ever need to roll back prompts.

### 3c. Code patterns worth copying when writing the CLI

- `WordPressPostsResult` (in `wordpress_client.py`) — typed paginated result with `cache_unchanged` / `x_wp_total` / `first_page_hash`. Same pattern can be reused from the CLI.
- `format_discord_content_for_wordpress` (`publisher.py`) — paragraph formatter that splits on blank lines, html-escapes per `<p>`, joins with `\n\n`. Even though the CLI is writing music metadata (not prose) the convention here is: escape user-supplied strings, do not trust HTML, use `<p>` / `<br />` for line breaks.
- `Publisher.trash_post` — example of `delete_post(force=False)` followed by `refresh_post_cache(force=True)`. Any state-changing CLI action against WP should be followed by a post-cache refresh or it will diverge from the tracker.
- Tag create + reconciliation in `WordPressClient.create_tag` — handles the `term_exists` 400 by re-resolving the existing tag. **The CLI must do the same** — WP returns 400 for duplicate-tag creation, not 409, and the body uses `code`/`data.term_id` to surface the existing ID.
- Category seeding in `_ensure_categories` (`publisher.py`) — required categories are `["Album", "EP", "Single", "Compilation", "Relisten"]` and the cache key is `release_type.value` (e.g. `"EP"`, not `"ep"`).

---

## 4. Metadata the CLI should consider writing to each post

These are the fields that exist in the local DB / Spotify snapshot but are **not currently written to the WP post**. Listing them with their source so the recipient of these notes can pick what to ship.

Direct high-value additions (single string fields, easy to add to post body or post meta):

| Source field                                                                | Where                                                        | Notes                                                                     |
|-----------------------------------------------------------------------------|--------------------------------------------------------------|---------------------------------------------------------------------------|
| `release_date`                                                              | `release_lifecycle.release_date`                             | Often YYYY or YYYY-MM-DD — keep Spotify's original format                 |
| `total_duration_ms`                                                         | `release_lifecycle.total_duration_ms`                        | Format as millisecond count or `HH:MM:SS`                                |
| `total_tracks`                                                              | `release_lifecycle.total_tracks`                             | Useful for "12 tracks, 47 min" summaries                                 |
| Spotify URL / URI                                                           | constructed from `spotify_id`                                | <https://open.spotify.com/album/{spotify_id}>                              |
| Artist Spotify URLs                                                         | per-row in `release_artist` (`spotify_id`)                   | <https://open.spotify.com/artist/{spotify_id}>                              |

Track listing (already fully normalized in DB):

- For each `release_track` row: `<disc_number>-<track_number>. <title> (<mm:ss>)`
- `disc_number` and `track_number` are both stored; multi-disc discs need grouping.

Cover-art refinements:

- The cover is already uploaded as featured media with alt_text = `"{title} album art"`. The CLI can update to e.g. `"{title} by {primary_artist} — cover art"` via `update_media()` if richer alt-text is desired.

Content / body additions:

- Current `content` is empty placeholder. The track listing + duration summary + Spotify deep link is the typical retrofit.
- Line-ending convention: the existing formatter uses `\n\n` between paragraphs. Body paragraphs should be inside `<p>...</p>` for prose; `<br />` for line-breaks inside a paragraph; metadata lines are typically written as a small HTML block, not markdown.

Math the CLI should reuse verbatim (so the WP-side plugin agrees):

- Total duration formatter: `<total_ms> // 1000` for seconds, then `HH:MM:SS` (or `MM:SS` if under an hour).
- Track-length formatter: `mm:ss` from `duration_ms`.

Reuse the same category scheme:

- The post's primary category is one of `{Album, EP, Single, Compilation}` — set on publish. If a CLI notices a post whose primary category does not match the computed `release_type`, that is a real bug worth reporting.
- The optional secondary category is `Relisten` — present iff `release_lifecycle.is_relisten == 1`.

Tag/artist identity:

- Post tags today are artist names, exactly. If the CLI adds new tags (e.g. year tags, label tags, genre), it must extend `tag_cache`-style reconciliation so dedup stays accurate; otherwise new tags will silently become "the post contains artist X" from the tracker's perspective.

---

## 5. Pitfalls the CLI writer should know about

- The DB uses `INTEGER` primary keys (AUTOINCREMENT) and TEXT for the `status` / `release_type` columns holding enum values **as the literal string**. Always read/write via the enum class in `src/models.py` (`LifecycleStatus`, `ReleaseType`, `PromptState`, `PromptType`) to avoid drift.
- DATETIMEs are stored as ISO 8601 strings (e.g. `"2024-12-01T15:04:05.123456"`), not epoch. `database.py` reads them with `datetime.fromisoformat(row[...])`.
- Artist names are normalized **after removing commas** (`normalize_artist_name` strips `,` first). If the CLI re-derives artist lists for comparison it must do the same, or duplicate-detection will skew.
- `wordpress_post_cache` stores `artists` as a JSON list of tag names (the names of words assigned as tags), not as Spotify artist objects. The CLI should not treat this as Spotify-grade data.
- A published release row in `release_lifecycle` is deleted after 24h **by policy**. Always read the link from `saved_library_album.wordpress_post_id`, not `release_lifecycle.wordpress_post_id`, for any post that has been around longer than that window.
- `update_post` is a `POST` to `/posts/{id}`, not PATCH. The CLI should not assume PUT semantics.
- WordPress returns 400 (not 409) when creating a duplicate tag. Use the `term_exists` / `existing_term_slug` / `term_exists_invalid` branch from `WordPressClient.create_tag`.
- Media uploads must drop the `Content-Type: application/json` header (see `_upload_artwork`). Any CLI that reuses the same `httpx.AsyncClient` should follow that pattern.
- The `X-WP-Total` header is sometimes truncated (`--` on certain WP versions if there are 10k+ items). The CLI's pagination should brace for that and fall through to short-page detection like the tag fetch does.
- After any CLI write to the WP API that **changes the post count or a post's title/tags** (delete, create, retag), call `Publisher.refresh_post_cache(force=True)` (or replicate its logic) — otherwise the local `wordpress_post_cache` and `service_state.wordpress_post_cache.*` keys will drift from WP, and the next auto-publish by the tracker will have a stale duplicate view.
