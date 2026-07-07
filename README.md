# WordPress Post → Album CLI

A verbose, single-file Python CLI that walks every blog post on your WordPress
site, treats each post as an album, fuzzy-matches it against **Spotify**,
falls back to **Last.fm** for genre and mood tags, and backfills **every
auto-fillable SCF (Secure Custom Fields) field** plus the `artist`, `genre`,
and `release_type` custom taxonomies.

It is purpose-built for the workflow where **one WordPress post = one music
release**: the post title is the release name, the `post_tag`s are the
artists, and the WP category (`Album` / `EP` / `Single` / `Compilation`)
serves as the release type. The CLI keeps that shape — it never overwrites
human-curated fields and never touches media.

---

## Table of contents

1. [What it does](#what-it-does)
2. [Features](#features)
3. [Data sources](#data-sources)
4. [Architecture](#architecture)
5. [Prerequisites](#prerequisites)
6. [Installation](#installation)
7. [Configuration](#configuration)
8. [Usage](#usage)
   - [`run` subcommand](#run-subcommand)
   - [`stats` subcommand](#stats-subcommand)
   - [`fuzzy` subcommand](#fuzzy-subcommand)
9. [The algorithm, in one screen](#the-algorithm-in-one-screen)
10. [Field map: auto-fillable vs skip](#field-map-auto-fillable-vs-skip)
11. [Taxonomies](#taxonomies)
12. [Idempotency & safety](#idempotency--safety)
13. [Dry-run output files](#dry-run-output-files)
14. [Warnings (last.fm / Spotify edge cases)](#warnings-lastfm--spotify-edge-cases)
15. [What this tool deliberately does NOT do](#what-this-tool-deliberately-does-not-do)
16. [File layout](#file-layout)
17. [Recommended workflow](#recommended-workflow)
18. [Troubleshooting](#troubleshooting)
19. [Related documentation](#related-documentation)

---

## What it does

Given an existing WordPress post like:

> **Title:** _Absolution_
> **Tags:** `Muse`
> **Category:** `Album`

…the CLI will:

1. Pull the post from WordPress (already includes `tags`, `date`, and partially
   filled ACF/SCF data if `?context=edit`).
2. Normalize the title and tag list.
3. Hit Spotify's **search → album → paginated tracks** ladder to find the
   exact release and pull raw album + per-track metadata.
4. Hit Last.fm's **`album.getinfo`** for genre / mood tags (Spotify's built-in
   `album.genres[]` is **not used** — Last.fm is more reliable for that).
5. Compose an SCF payload that fills in only the fields that are currently
   empty on the WordPress side.
6. Mirror the existing `post_tag` names into the new `artist` taxonomy,
   write the top-3 Last.fm tags into the `genre` taxonomy, and write exactly
   one term into the `release_type` taxonomy (`Album` / `EP` / `Single` /
   `Compilation`).
7. Either dump the planned patches to `./out/planned_patches.json` for review
   or POST the changes live to WordPress.

Verbose, single-threaded, dry-run-by-default, and idempotent on re-run.

---

## Features

- ✅ **Verbose logging** at `INFO` level by default; bump to `DEBUG` with `-v`.
  Every Spotify and Last.fm call is traced with its HTTP status, params, and
  excerpted payload.
- ✅ **Dry-run-first**: `--dry-run` is the default. Nothing is written to
  WordPress until you pass `--apply` explicitly.
- ✅ **Idempotent**: re-running the CLI is safe. It skips posts where every
  auto-fillable field is already non-empty, and for partial posts it never
  overwrites an already-populated value.
- ✅ **Stdlib only**. No `pip install` required — only the Python 3.10+
  standard library is used (`urllib`, `json`, `argparse`, `logging`, etc.).
- ✅ **Single file**. Everything lives in `post_to_album.py`.
- ✅ **Catalog-only Spotify access** via the Client Credentials Flow — no
  OAuth user dance, no per-user quota scope, no tokens to manage.
- ✅ **Last.fm autocomplete + noise filter** for tags so that `"2024"`,
  `"aoty"`, `"seen live"`, `"favorites"`, `"under 2000"` don't pollute the
  `genre` taxonomy, and any tag that matches an artist name gets dropped
  too.
- ✅ **File-based dry-run artifacts**. `./out/planned_patches.json` is
  reviewable in any editor before you commit to applying.

---

## Data sources

| Source | Used for | Auth | Notes |
| --- | --- | --- | --- |
| **WordPress REST** (`/wp-json/wp/v2`) | post metadata, taxonomies, category seed, ACF/SCF fields | HTTP Basic with Application Password | must enable `?context=edit` to see `acf` block |
| **Spotify Web API** (Client Credentials) | album metadata, track list, per-track `explicit`, release date | `client_id` + `client_secret` from `.env` | token cached in-memory; auto-refresh on `expires_in`; respects `Retry-After` |
| **Last.fm API** | genre tags, mood tags, MBID | `api_key` from `.env` | read `tags.tag[]` (not the deprecated `toptags`) |

Spotify is the source of truth for tracklist, album-level data, dates, and
IDs. Last.fm is the source of truth for genre and mood tags only.

---

## Architecture

```
post-to-album/
├── post_to_album.py        # ALL logic — single file, stdlib only
├── example.env             # template for required environment variables
├── .env                    # (your actual values, gitignored)
└── out/                    # dry-run output + unresolved-match log
    ├── planned_patches.json
    └── unresolved.json
```

Everything is in one file by design — YAGNI. The CLI is subcommand-based:

```
python post_to_album.py run    --all | --limit N [--offset M]
                               [--dry-run | --apply]
                               [--out-dir DIR]
python post_to_album.py stats
python post_to_album.py fuzzy "title" ["artist" ...]
```

---

## Prerequisites

- **Python 3.10+** (uses PEP 604 union types `int | None`).
- A WordPress site with:
  - Custom post type `post` containing the releases (≈ 800 in the live
    catalog this tool was built against).
  - **Secure Custom Fields (SCF)** exposing the music fields. Full field
    map is in [`plan.md` §2](./plan.md).
  - Application Passwords enabled — generate one in
    `WP Admin → Users → Profile → Application Passwords`.
  - A user account with permission to edit posts and create taxonomy terms
    (typically an admin or an editor).
- A **Spotify app** registered at <https://developer.spotify.com/dashboard>
  to obtain a Client ID and Client Secret. Client Credentials Flow
  (catalog-only) is sufficient — no per-user OAuth.
- A **Last.fm API key** from <https://www.last.fm/api/account/create>.

---

## Installation

Clone the repository and you're done — no `pip install` step:

```bash
git clone <repo-url> Wordpres-PostToAlbum-Script
cd Wordpres-PostToAlbum-Script

# Make a real .env from the template
cp example.env .env
$EDITOR .env
```

That's it. There are no third-party dependencies.

---

## Configuration

All configuration is via environment variables. The CLI loads them from
`.env` (override with `--env path/to/.env`).

| Variable | Required for | Purpose |
| --- | --- | --- |
| `WORDPRESS_BASE_URL` | reads + writes | e.g. `https://example.com` — the CLI appends `/wp-json/wp/v2` itself. |
| `WORDPRESS_USERNAME` | writes (`run --apply`) | the WP user that owns the Application Password. |
| `WORDPRESS_APP_PASSWORD` | writes (`run --apply`) | the 24-char Application Password from WP Admin. |
| `LASTFM_API_KEY` | reads + writes | required for genre and mood tags. |
| `SPOTIFY_CLIENT_ID` | reads + writes | Spotify app Client ID. |
| `SPOTIFY_CLIENT_SECRET` | reads + writes | Spotify app Client Secret. |

> 🛑 **Read-only mode works without `WORDPRESS_USERNAME` and
> `WORDPRESS_APP_PASSWORD`** (e.g. for `--dry-run` and `stats`). The CLI
> will refuse `--apply` if they are missing.

See [`example.env`](./example.env) for the canonical template.

---

## Usage

### `run` subcommand

Process a batch of posts and either dry-run or apply.

```bash
# 1) Stats first — see how empty the ACF fields are today.
python post_to_album.py stats

# 2) Dry-run a small window — the default mode is --dry-run.
python post_to_album.py run --limit 25

# 3) Eyeball the planned patches.
less out/planned_patches.json

# 4) Dry-run the whole catalog (default if no --limit).
python post_to_album.py run --all

# 5) Once you trust it — apply for real.
python post_to_album.py run --apply --limit 5     # try 5 first
python post_to_album.py run --apply --all
```

Flags:

| Flag | Default | Notes |
| --- | --- | --- |
| `--all` | `--all` if `--limit` is absent | process every post. |
| `--limit N` | none | process at most `N` posts. |
| `--offset M` | `0` | skip the first `M` posts (handy for paging through). |
| `--dry-run` | **default if neither is passed** | write `./out/planned_patches.json` only. |
| `--apply` | off | write to WordPress for real. LP/HP/`PATCH`/etc. |
| `--out-dir DIR` | `out` | override the directory for dry-run JSON. |
| `--env PATH` | `.env` | override the env file path. |
| `-v` / `--verbose` | off | DEBUG-level logging. |
| `--quiet` | off | WARNING-level logging (suppress INFO). |

`--dry-run` and `--apply` are **mutually exclusive**. The CLI errors out
if both are passed.

### `stats` subcommand

Report the field fill-rate for every auto-fillable SCF field and the three
taxonomies. Run this **before** and **after** a `--apply` pass to see what
changed.

```bash
python post_to_album.py stats -v     # inspect which posts are already filled
```

### `fuzzy` subcommand

Debug-search Spotify for a title + artist combination, then print the top
candidates and the score each was assigned. Use this when a post shows up
in `out/unresolved.json` and you want to figure out why.

```bash
python post_to_album.py fuzzy "Absolution" "Muse"
python post_to_album.py fuzzy "My Beautiful Dark Twisted Fantasy" "Kanye West"
```

---

## The algorithm, in one screen

For each post the CLI goes through:

1. **Read WP inputs.** `title`, `post_tag` names, `date`, and existing
   ACF/SCF (`?context=edit`).
2. **Skip-if-fully-filled.** If every auto-fillable field is already
   non-empty, log `SKIP` and move on. Otherwise, keep going — but never
   overwrite a non-empty value later.
3. **Normalize.** Strip diacritics, drop parenthetical "Remastered", etc.,
   lowercase.
4. **Spotify ladder.** `q=album:norm_title norm_artist` →
   `q=album:norm_title` → `q=norm_title`, with `limit=10&market=US`.
5. **Rank candidates** with `difflib.SequenceMatcher` against the
   normalized title and each known artist.
6. **Fetch full album + paginate `tracks.next`** (`limit=50`).
7. **Last.fm `album.getinfo`** for genre and mood tags (`autocorrect=1`).
8. **Apply the Last.fm blocklist**, drop artist names that appear as tags,
   keep the top 3.
9. **Compute** track rows, total length, average, release-type heuristic.
10. **Build the PATCH body** for ACF + `categories` + `artist` + `genre` +
    `release_type` — one POST updates everything in WordPress.
11. **Apply** — either dump to `out/planned_patches.json` or POST live.

For the per-track SCF sub-fields, see [`plan.md` §2b](./plan.md).

---

## Field map: auto-fillable vs skip

The CLI distinguishes between three statuses per SCF field:

- **auto** — always written if currently empty.
- **auto-if-empty** — written if currently empty, with a special default
  explained below.
- **skip** — never touched. These are human-curated.

| SCF field | Status | Source |
| --- | --- | --- |
| `music_tracks` (repeater: `disc_number`, `track_number`, `title`, `duration_ms`, `spotify_id`, `highlight`, `explicit`) | auto | Spotify `GET /v1/albums/{id}/tracks` |
| `music_length_ms` | auto | `sum(track.duration_ms)` |
| `spotify_album_id` | auto | `album.id` |
| `spotify_album_url` | auto | `https://open.spotify.com/album/{id}` |
| `music_release_date` | auto | `album.release_date` (Spotify's native precision) |
| `music_listened_at` | auto-if-empty | `post.date[:10]` — only writes when the SCF field is currently blank |
| `lastfm_release_id` | auto | Last.fm `mbid` |
| `music_total_tracks` | auto | `album.total_tracks` |
| `music_avg_track_ms` | auto | `music_length_ms / music_total_tracks` |
| `music_explicit` | auto | `any(track.explicit == True)` across `music_tracks` |
| `music_mood_tags` (repeater: `mood`) | auto | Last.fm top-3 tags after blocklist (may be empty) |
| `listen-count` | auto-if-empty | defaults to `1` when blank — bump by hand for relistens |
| `music_rating` | **skip** | human |
| `music_favorite` | **skip** | human |
| `music_notes` | **skip** | human |
| `unreleased` | **skip** | human |
| `highlight` (per track) | **skip** | hard-coded `false` initially, human-editable flag |

> 💡 The CLI will **never overwrite** a non-empty value, even when the
> heuristic would compute a different one. Re-running with a cleaner data
> source will not stomp your hand-curated values.

---

## Taxonomies

| Taxonomy | Source | Notes |
| --- | --- | --- |
| `artist` | existing `post_tag` names, **1:1 passthrough** | `"Drake & 21 Savage"` becomes a single artist term with that exact name and slug — no string splitting. |
| `genre` | **Last.fm only** (Spotify `album.genres[]` is **not** used) | top-3 from `tags.tag[]` after the blocklist filter. May be empty. |
| `release_type` | computed from track count + `album_type` | exactly **one** term per post: `Album`, `EP`, `Single`, or `Compilation`. Marker terms like `Relisten` / `Unreleased` / `Concert` are not written into this taxonomy. |

The CLI also mirrors the resolved release type into the legacy WP
`category` field for backward compatibility of admin filter dropdowns.

---

## Idempotency & safety

Two layers:

1. **Whole-post skip.** If every auto-fillable SCF field is already
   non-empty, the CLI logs `SKIP post N '<title>' (fully filled)` and
   moves on. This is checked at the top of every `enrich()` call.
2. **Per-field non-overwrite.** Inside the patch body, every field is
   gated by `_set_if_empty`. Even when a post is partially filled, only
   currently-blank fields are written. Existing values are preserved
   verbatim.

This makes the CLI safe to re-run indefinitely. The first `--apply`
backfills the catalog; subsequent runs either skip or fill in only the
fields that were left blank by Last.fm or Spotify returning nothing.

---

## Dry-run output files

Two JSON files are produced by every dry-run:

- **`out/planned_patches.json`** — a JSON array of `{post_id, body}` that,
  if you were applying, would be POSTed to WordPress. Delete this file
  once you've applied; the CLI does not consume it automatically.
- **`out/unresolved.json`** — a JSON array of `{post_id, title,
  top_5_candidates}` for every post where Spotify search returned nothing
  usable. This is your triage list.

You can override the directory with `--out-dir DIR` (handy for batched
runs you want to compare side-by-side).

```bash
python post_to_album.py run --limit 50 --out-dir out-batch-1
python post_to_album.py run --limit 50 --offset 50 --out-dir out-batch-2
diff <(jq 'map(.post_id)' out-batch-1/planned_patches.json) \
     <(jq 'map(.post_id)' out-batch-2/planned_patches.json)
```

---

## Warnings (last.fm / Spotify edge cases)

- **Spotify `album.explicit` is generally `null`.** `music_explicit` is
  computed from `any(track.explicit == True)` across the tracklist, not
  from the album-level field.
- **Last.fm `tags.tag[]` may be returned as a single object** when only
  one tag is available — the CLI coerces it to a list defensively.
- **Last.fm is noisy.** Year tags, `"aoty"`, `"seen live"`,
  `"favorites"`, and `'under NNNN'` are dropped by an explicit blocklist.
  Any tag whose name matches one of the post's existing artist names is
  also dropped — `"Radiohead"` is not a genre of a Radiohead album.
- **Partial Spotify `release_date`** (`YYYY` or `YYYY-MM`) is coerced to
  `YYYY-01-01` / `YYYY-MM-01` before writing, because SCF rejects anything
  other than `d/m/Y` for date pickers.
- **No empty numerics.** Sending `""` for `music_length_ms` etc. yields
  HTTP 400. The CLI never writes a numerically empty value.
- **Last.fm returns no useful tags → empty fields + a warning.** The CLI
  does **not** fall back to Spotify `album.genres[]`. The
  `music_mood_tags` and `genre` taxonomy end up empty, and a `WARN:` line
  appears in stderr along with the post being added to `unresolved.json`.

See [`plan.md` §9](./plan.md) for the full empirical-research results
that drove these rules.

---

## What this tool deliberately does NOT do

To keep the scope tight, the CLI does **not**:

- Touch cover images, `featured_media`, or any media fields. The
  [Album Art Picker V2 plugin](./album-art-picker-v2-analysis.md) keeps
  handling visuals.
- Write to the `album` custom post type (still empty in this WP install —
  the migration question is out of scope for now).
- Open Spotify `album.genres[]` — Last.fm is the single source of genre.
- Fall back to MusicBrainz / Deezer / Discogs for genre (Last.fm only).
- Run an OAuth user-flow — Client Credentials is enough for catalog data.
- Detect relistens (multiple posts for the same release). The first run
  pumps `listen-count=1`; relistens are bumped manually in WP admin after.
- Set the `highlight` flag on individual tracks — that's a human-curated
  field.
- Touch `music_rating`, `music_favorite`, `music_notes`, `unreleased` —
  these are yours.

See [`plan.md` §7](./plan.md) for the full YAGNI scope.

---

## File layout

```
Wordpress-PostToAlbum-Script/
├── README.md                          ← this file
├── plan.md                            ← canonical, locked-in design doc
├── vision.md                          ← the original project brief
├── questions.md                       ← every answered question, with rationale
├── example.env                        ← env file template
├── .gitignore
├── post_to_album.py                   ← the single-file CLI
├── album-art-picker-v2-analysis.md    ← how cover images get picked (context only)
├── spotify-album-blog-tracker-analysis-report.md
│                                      ← how another process interpreted WP posts
├── scf-export-2026-07-05.json         ← current SCF field map
├── scf-export-field-meanings.md       ← per-field plain-English meanings
└── out/                               ← dry-run outputs (gitignored)
    ├── planned_patches.json
    └── unresolved.json
```

---

## Recommended workflow

A first-time end-to-end run:

```bash
# 1. Bootstrap env
cp example.env .env && $EDITOR .env

# 2. Look before you leap
python post_to_album.py stats -v
python post_to_album.py fuzzy "Absolution" "Muse"

# 3. Tiny dry-run — verify the planned_patches.json shape
python post_to_album.py run --limit 3
less out/planned_patches.json

# 4. Apply to the first few posts, watch the WP admin for them
python post_to_album.py run --apply --limit 5

# 5. Review what changed
python post_to_album.py stats

# 6. Apply to the rest
python post_to_album.py run --apply --all

# 7. Triage the unresolved
jq '. | length' out/unresolved.json      # how many posts couldn't be matched?
python post_to_album.py fuzzy "Weird Title" "Some Artist"   # figure out why
```

---

## Troubleshooting

**`Spotify auth failed`** — the CLI will tell you which env var it
couldn't find. Make sure `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`
are both set, and that the app has been registered with the right
catalog scope (default Client Credentials grants catalog-only).

**`WordPress 401 Unauthorized`** — check `WORDPRESS_BASE_URL` (no
trailing slash), `WORDPRESS_USERNAME`, and `WORDPRESS_APP_PASSWORD`.
Application Passwords are 24 chars and include spaces, copy-paste the
exact string.

**`ACF field "xyz" returned 400`** — usually means SCF is rejecting an
empty value for a numeric key. Run with `-v` and look for the offending
field. Empty numeric fields are skipped silently; this typically means
the post body reached WordPress with a `null` where SCF expected a
number.

**`every post shows SKIP`** — your ACF is fully filled already. Run
`python post_to_album.py stats -v` to inspect per-field fill rates and
make sure you're targeting the SCF fields this CLI expects. If you've
migrated to a different SCF schema, regenerate the field list
(`scf-export-2026-07-05.json`) and confirm the key names match.

**`unresolved.json` is huge** — open one entry, copy the `top_5_candidates`,
and use `python post_to_album.py fuzzy …` to see why the ranker rejected
them. Common causes: tag delimiter ("Drake & 21 Savage" vs "Drake"),
punctuation (vinyl record sides, multi-disc releases), or a release
that genuinely isn't on Spotify.

**`429 Too Many Requests` from Spotify** — the CLI respects
`Retry-After` and retries once. If you see this in `-v` output a lot,
slow down with `--limit` paging and wait between batches.

---

## Related documentation

- [`vision.md`](./vision.md) — the original brief that kicked off the
  project.
- [`plan.md`](./plan.md) — the locked, canonical design doc. Read this
  if you're changing the algorithm or the field map.
- [`questions.md`](./questions.md) — every design question the user was
  asked + the answer that was chosen.
- [`album-art-picker-v2-analysis.md`](./album-art-picker-v2-analysis.md)
  — context on the album-cover workflow (left running untouched).
- [`spotify-album-blog-tracker-analysis-report.md`](./spotify-album-blog-tracker-analysis-report.md)
  — context on how another process interprets WP posts.
- [`scf-export-field-meanings.md`](./scf-export-field-meanings.md) —
  per-field, plain-English meanings for the SCF schema in
  `scf-export-2026-07-05.json`.
