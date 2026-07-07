# Plan — WordPress Post → Album CLI (SCF metadata backfill) — **v2**

> **Goal recap (from `vision.md` + `scf-export-field-meanings.md` + `questions.md`):** a verbose Python CLI that walks a batch of WordPress posts, fuzzy-matches each one against Spotify, falls back to **Last.fm only** for genre + mood tags, fills every auto-fillable SCF field, mirror-copies artist names (1:1, no splitting) into the new `artist` taxonomy, drives the `genre` taxonomy from **Last.fm tags only**, and writes exactly one term per post into the `release_type` taxonomy (`Album` / `EP` / `Single` / `Compilation`).
>
> **Dry-run writes a JSON diff to `./out/`; live writes POST/PATCH the post.**

---

## 1. Reconnaissance summary (verified live)

| Item | Confirmed |
| --- | --- |
| Total posts (post type `post`) | **807** across 9 pages of 100; `X-WP-Total` reliable |
| New: `Albums` custom post type (`album`) | **Registered, but 0 posts in it today** — `GET /wp/v2/album?per_page=5` returns `[]` |
| Categories seeded | Album (6) · Compilation (98) · EP (7) · Relisten (93) · Single (5) · Uncategorized (1) · Unreleased (200) |
| Custom taxonomies | `artist`, `genre`, `release_type` — all registered but empty today |
| Default tags | `post_tag` — populated (1+ per post); some are joined (`"Drake & 21 Savage"`-style) but most align with a real Spotify artist name |
| ACF populated today | Only `music_listened_at` + `music_match_confidence` on 10 posts (both fields in stale export — `music_match_confidence` is **removed** in the new SCF export) |
| Repeater location in REST | Top-level `acf` block, only with `?context=edit`, sibling `<f>_source` per field |
| **Dupe-key SCF bug** | **Fixed** in new export — `disc_number`/`track_number`/`duration_ms`/`spotify_id`/`highlight` no longer have duplicate definitions |
| WP auth | HTTP Basic, precomputed `Basic <base64>` header on every JSON request |
| Spotify Web API | **Client Credentials** via `POST https://accounts.spotify.com/api/token` with `client_id`/`client_secret` from `.env`. Token TTL 3600 s; CLI auto-refreshes. 429s with `Retry-After`. |
| Last.fm API | `GET https://ws.audioscrobbler.com/2.0/?method=…&api_key=…&format=json`. **Correction: `toptags` is null in 2026; real tags are in `tags.tag[]`.** Noisy (year tags, "aoty", artist names). Needs blocklist. |
| Album-art-picker analysis | Source of query ladder, normalisation rules; CLI reuses normalisation but skips image-hash/OCR |
| Spotify-blog-tracker analysis | Source of release-type heuristic (1–3 / 4–6 / 7+ tracks + 30 min / 10 min), pagination conventions |

---

## 2. SCF field → source mapping (after user updates)

> Auto-fillable = CLI writes. Skip = CLI never touches. Reflect the **new** SCF export (2026-07-05).

### 2a. Group `meta`

| # | SCF field | Type | Status | Source / call | Transform |
| --- | --- | --- | --- | --- | --- |
| 1 | `music_tracks` (repeater) | rows | **auto** | Spotify `GET /v1/albums/{id}/tracks` (paginate `tracks.next`) | Each row: `{disc_number, track_number, title, duration_ms, spotify_id, highlight:false, explicit}` (per-track explicit from Spotify track fields, highlight default False) |
| 2 | `music_rating` | number | **skip** | human | 0–100 (per meanings doc). Never overwrite. |
| 3 | `music_favorite` | true_false | **skip** | human | Never overwrite. |
| 4 | `music_length_ms` | number | **auto** | derived from `music_tracks` rows | `sum(duration_ms)` |
| 5 | `spotify_album_id` | text | **auto** | `album.id` | raw |
| 6 | `spotify_album_url` | url | **auto** | constructed | `https://open.spotify.com/album/{id}` |
| 7 | `music_release_date` | date_picker | **auto** | `album.release_date` | keep Spotify's raw precision (YYYY or YYYY-MM or YYYY-MM-DD) |
| 8 | `music_listened_at` | date_picker | **auto-if-empty** | `post.date` (WordPress post publish date) | YYYY-MM-DD string. **Per field meanings: "copy from when the blog post was posted"** — so this is a free auto-fill. CLI only writes if currently empty. |
| 9 | `lastfm_release_id` | text | **auto** | `album.mbid` from `album.getInfo` | raw UUID if non-empty |
| 10 | `music_total_tracks` | number | **auto** | `album.total_tracks` | integer |
| 11 | `music_avg_track_ms` | number | **auto** | `music_length_ms / music_total_tracks` | integer |
| 12 | `music_explicit` | true_false | **auto** | `any(track.explicit == True for track in music_tracks)` | bool (album-level = OR across tracks) |
| 13 | `music_mood_tags` (repeater) | rows | **auto** | Last.fm `tags.tag[]` (no Spotify fallback) | top 3 tags after blocklist filter; each row `{mood: <tag>}` |
| 14 | `music_notes` | text | **skip** | human | Never overwrite. |
| 15 | `unreleased` | true_false | **skip** | human | per meanings doc: "whether or not this release is unreleased from an artist." Trust the existing value. |
| 16 | `listen-count` | number | **auto-if-empty** | default `1`, else revisit on rerun | Per meanings doc: index of how many times you listened. Most posts = 1. Relistens = N. **Default to 1 when filling empty.** Detection of 2+ is a separate question (see questions.md Q3). |

### 2b. Per-track sub-fields on `music_tracks`

| SCF field | Type | Source | Notes |
| --- | --- | --- | --- |
| `disc_number` | number | Spotify track | integer |
| `track_number` | number | Spotify track | integer |
| `title` | text | Spotify track `name` | raw, but normalise " - Remastered 2009" style froth if it shows up spotify-side (rare) |
| `duration_ms` | number | Spotify track `duration_ms` | integer |
| `spotify_id` | text | Spotify track `id` | raw 22-char base62 |
| `highlight` | true_false | hard-coded `false` initially | human flag, CLI doesn't second-guess |
| `explicit` | true_false | Spotify track `explicit` | the new per-track field, different from album-level `music_explicit` |

### 2c. Custom taxonomies (locked)

| # | Taxonomy | Status | Source | Transform |
| --- | --- | --- | --- | --- |
| T1 | `artist` | **auto** | Existing `post_tag` values on the post | One term per existing tag, find-or-create. **1:1 passthrough, do NOT split** (Q5 answer). Names preserved verbatim. |
| T2 | `genre` | **auto** | **Last.fm only** — `album.getinfo` `tags.tag[]` (Q5=III, Q6=A). Spotify `album.genres[]` is **not used** (per Q5: "Spotify genres are unreliable"). | Top 3 from Last.fm after blocklist (drop `^\d{4}$`, "aoty", "best of <year>", artist-names appearing as tags). |
| T3 | `release_type` | **auto** | `compute_release_type(tracks, raw_spotify_type)` | **Exactly one term** per post (Q1=a) — `Album` / `EP` / `Single` / `Compilation`. No marker terms (`Relisten`, `Unreleased`, `Concert`) attached; those live in SCF metadata only. |

### 2d. Post categories

CLI mirrors `category` (using the existing 7-seeded ids) for **admin filter backward compat** only. Per Q1, the *primary* release-type record is the `release_type` taxonomy; the `category` is a shadow copy:

| Spotify-derived type | category id | category name | release_type term slug |
| --- | --- | --- | --- |
| Album | 6 | Album | `album` |
| EP | 7 | EP | `ep` |
| Single | 5 | Single | `single` |
| Compilation | 98 | Compilation | `compilation` |

(Unreleased/Relisten/Concert are **not** in the `release_type` taxonomy; they remain in SCF metadata as `unreleased` true_false. `previous-listen-posts` was removed from SCF, so we have no SCF relisten bool — detection of relisten is now **out of scope** for v1.)

(Catalogue reality: live WP today has `release_type` taxonomy but zero terms in it. First run will see the four terms (Album/EP/Single/Compilation) created on demand; `Release Types` taxonomy terms get seeded by the find-or-create logic during the dry-run.)

---

## 3. Architecture (one file, YAGNI)

```
post-to-album/
├── post_to_album.py        # ALL logic — single file
├── .env                    # WORDPRESS_BASE_URL + _USERNAME + _APP_PASSWORD
│                           # LASTFM_API_KEY + SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET
└── out/                    # dry-run output + unresolved match log
    └── planned_patches.json
    └── unresolved.json
```

`.env` already includes `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` — no local token file, no OAuth subcommand. Spotify **Client Credentials Flow** only (server-to-server, catalog-only access).

Stdlib only (`urllib.request`, `json`, `argparse`, `logging`, `http.server`). No new dependencies.

CLI subcommands (argparse, locked in per Q15):

```
python post_to_album.py run   --all | --limit N              (with --offset M)
                              [--dry-run | --apply]
                              [--only-empty-field=...]       # power-user flag
python post_to_album.py stats        # ACF + taxonomy fill-rate before/after
python post_to_album.py fuzzy "title" "artist"   # debug-search Spotify
```

`--dry-run` and `--apply` are mutually exclusive; one is required. Default `--all` if no `--limit`.

Single-threaded (Q13=iii): 1 req/s for WP, sequential Spotify (well inside dev-mode quota). Stdlib `logging` at DEBUG to stderr, one-line summary at INFO.

### Algorithm (per-post, locked)

```python
AUTO_FILLABLE_FIELDS = (
    "music_tracks", "music_length_ms", "spotify_album_id", "spotify_album_url",
    "music_release_date", "music_listened_at", "lastfm_release_id",
    "music_total_tracks", "music_avg_track_ms", "music_explicit",
    "music_mood_tags", "listen-count",
)

def is_fully_filled(acf):
    return all(_field_present(acf, f) for f in AUTO_FILLABLE_FIELDS)

def enrich(post):
    # 1) Read WP inputs (loaded as part of bulk fetch)
    title = post['title']['rendered']
    existing_post_tags = lookup_tag_names(post['tags'])     # 1:1 passthrough
    post_date = post['date']                                # ISO 8601
    acf_in = post.get('acf', {}) or {}

    # 2) Idempotency skip-check (Q10 = β: skip if EVERY auto-fillable field filled)
    if is_fully_filled(acf_in):
        log("SKIP post %d '%s' (fully filled)", post['id'], title)
        return
    # Otherwise we know at least one auto field is empty. We still skip any
    # individual field that is non-empty — never overwrite existing values.

    # 3) Normalize title + tags (analysis.md §3.B/C combined)
    q_title   = norm_title(title)
    q_artists = [norm_artist(a) for a in existing_post_tags]

    # 4) Spotify search ladder (analysis.md §5c, simplified to 3 rungs)
    candidates = spotify_search_ladder(q_title, q_artists)

    # 5) Pick best candidate — difflib-based ranking
    best = rank_candidates(candidates, q_title, q_artists)
    if best is None:
        append_unresolved(post, candidates[:5])
        log_warn_no_match(post)
        return
    # 6) Fetch full album detail + paginate tracks
    album = spotify_album(best['id'])
    tracks = spotify_album_all_tracks(best['id'])   # follows tracks.next

    # 7) Last.fm ONLY — genre + mood (Q5=III, Q6=A)
    album_info = lastfm_getinfo(album['artists'][0]['name'], album['name'])  # autocorrect=1
    lfm_tags   = pick_top_tags(album_info, max=3, blocklist=LFM_BLOCKLIST)   # dedup noise
    if not lfm_tags:
        log_warn(post['id'], "no useful Last.fm tags for %s — leaving empty", album['name'])

    # 8) Compute ACF fields (Q9=a — only fill music_listened_at if currently empty)
    track_rows = [
        {"disc_number":  t["disc_number"],
         "track_number": t["track_number"],
         "title":        t["name"],
         "duration_ms":  t["duration_ms"],
         "spotify_id":   t["id"],
         "highlight":    False,
         "explicit":     bool(t["explicit"])}
        for t in tracks
    ]
    length_ms         = sum(t["duration_ms"] for t in track_rows)
    release_type_term = compute_release_type(track_rows, album["album_type"])   # Album|EP|Single|Compilation

    acf_out = {}
    _set_if_empty(acf_in, acf_out, "music_tracks",       track_rows)
    _set_if_empty(acf_in, acf_out, "music_length_ms",    length_ms)
    _set_if_empty(acf_in, acf_out, "spotify_album_id",   album["id"])
    _set_if_empty(acf_in, acf_out, "spotify_album_url",  f"https://open.spotify.com/album/{album['id']}")
    _set_if_empty(acf_in, acf_out, "music_release_date", album["release_date"])
    _set_if_empty(acf_in, acf_out, "music_listened_at",  post_date[:10])          # YYYY-MM-DD
    _set_if_empty(acf_in, acf_out, "lastfm_release_id",  album_info.get("mbid", ""))
    _set_if_empty(acf_in, acf_out, "music_total_tracks", album["total_tracks"])
    _set_if_empty(acf_in, acf_out, "music_avg_track_ms", length_ms // album["total_tracks"] if album["total_tracks"] else 0)
    _set_if_empty(acf_in, acf_out, "music_explicit",     any(t["explicit"] for t in track_rows))
    _set_if_empty(acf_in, acf_out, "music_mood_tags",    [{"mood": t} for t in lfm_tags])
    _set_if_empty(acf_in, acf_out, "listen-count",       acf_in.get("listen-count", 1))   # Q3=A — default 1

    # 9) Find-or-create taxonomy terms
    artist_term_ids = upsert_terms("artist",       existing_post_tags)        # 1:1
    genre_term_ids  = upsert_terms("genre",        lfm_tags)                  # may be 0..3
    rt_term_ids     = upsert_terms("release_type", [release_type_term])       # exactly 1

    # 10) Mirror to legacy WP category (transitional, locked per §2d)
    cat_ids = [CATEGORY_MAP[release_type_term]]

    # 11) Build PATCH body
    body = {
        "acf":          acf_out,
        "categories":   cat_ids,
        "artist":       artist_term_ids,
        "genre":        genre_term_ids,
        "release_type": rt_term_ids,
    }

    # 12) Write or queue — single POST per second
    if dry_run:
        append_to("out/planned_patches.json", {"post_id": post["id"], "body": body})
    else:
        wp_update_post(post["id"], body)
```

`_set_if_empty` is the gate that makes the algorithm idempotent in two ways:

1. If the **whole post** is fully filled, the early `is_fully_filled` returns immediately (Q10=β).
2. If the post is **partially** filled, `_set_if_empty` only writes keys that are currently empty in the live WordPress state — we never overwrite an existing value with a freshly-computed one.

### Last.fm blocklist (Q5/Q11, draft list)

```
LFM_BLOCKLIST = [
    r"^\d{4}$",           # pure year tags ("2024")
    r"^aoty$",            # "aoty" / "album of the year"
    r"^best of \d{4}$",   # "best of 2024"
    r"^seen live$",
    r"^favorites?$",
    r"^under \d+$",       # "under 2000"
    r"^\w+\s*$",          # single-word / very short junk
]
```

Tags additionally filtered: any tag whose name appears in `existing_post_tags` (the artists) gets dropped — avoids "Radiohead" being used as a genre tag when there is an artist "Radiohead".

### State files

- `out/planned_patches.json` — list of `{post_id, body}` for the next `--apply`.
- `out/unresolved.json` — list of `{post_id, title, top_5_candidates}` for posts where Spotify returned nothing usable. (Q15.)

---

## 4. Improvements & decisions (locked-in from `questions.md`)

| # | Item | Decision (with question ref) |
| --- | --- | --- |
| A | **Skip human-curated fields** | Skip-list: `music_rating`, `music_favorite`, `music_notes`, `unreleased`, per-song `highlight`. Never overwrite. |
| B | **`music_listened_at`** | **Auto-fill ONLY if currently empty** — value `= post.date[:10]` (Q9=a). The 10 existing `20260617`-style posts are left alone. |
| C | **`listen-count`** | **Auto-fill ONLY if currently empty** — value `= 1` default (Q3=A). Relistens (`≥2`) are manually bumped post-run. |
| D | **`_source` companion fields** | Don't write them. SCF's Source feature handles attribution. **Empirically test on one throwaway post** before bulk (Q4=i) — outcome is documented in §9. |
| E | **Dry-run dump format & location** | `./out/planned_patches.json` (single JSON array). Consume-then-delete on apply. |
| F | **`release_type` taxonomy — single term per post** | One term from `{Album, EP, Single, Compilation}` based on `compute_release_type` heuristic. No `[Album, Relisten]`-style multi-term lists (Q1=a). Marker terms (Relisten/Unreleased/Concert) stay out of this taxonomy. |
| G | **`category` (legacy)** | Mirrors `release_type` taxonomy for backward compat of WP admin filter dropdowns (locked per §2d). |
| H | **`genre` taxonomy — Last.fm only** | Source is **only** Last.fm `album.getinfo` `tags.tag[]`. Spotify `album.genres[]` is **not used**. Blocklist applied (Q5=III, Q6=A). |
| I | **`music_mood_tags` — Last.fm top 3, may be empty** | Same Last.fm source as `genre`, but a separate cap of 3 with the same blocklist (Q5, Q11=c). Log warning + leave empty if LFM returns nothing useful. |
| J | **`artist` taxonomy — 1:1 passthrough** | CLI does not split tag strings. Reads `post_tag` names and creates `artist` taxonomy terms with the **exact same names and slugs**. If a tag is `"Drake & 21 Savage"`, the artist term is `"Drake & 21 Savage"` (single term). |
| K | **Spotify auth — env-based Client Credentials** | `.env` already has `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET`. CLI does **Client Credentials** only (no OAuth, no auth subcommand). Token cached per run, auto-refresh on `expires_in`. (Q7: user provides creds in env.) |
| L | **Idempotent re-run** | **β-policy** (Q10): skip post if **every** auto-fillable SCF field is non-empty. Otherwise process every post but never overwrite an already-populated value (`_set_if_empty`). |
| M | **Cover image / alt-text / media** | Don't touch. CLI ignores `featured_media` entirely. Album Art Picker V2 plugin left running untouched. (Q13=iii.) |
| N | **No parallelism** | Single-threaded, 1 req/s WP, sequential Spotify calls. Progress bar shows post-by-post. |
| O | **Unreleased posts** | Treated identically to any other post. CLI attempts Spotify lookup, fills fields, etc. (Q9.) |
| P | **Spotify field-filter ladder** | Spotify `q=album:"…" artist:"…"` only when the unquoted ladder rung returns zero. Conservative, avoids false negatives. |
| Q | **Output filenames** | `./out/planned_patches.json`, `./out/unresolved.json`. Configurable later if needed. |
| R | **Spotify quota mode** | Dev mode (Client Credentials). No extended quota requested. |
| S | **CLI invocation shape** | `--all`/`--limit N`, `--offset M`, mutually-exclusive `--dry-run`/`--apply`, optional `--only-empty-field=…`. No `--post-ids`. (Q15.) |

### Empirical research tasks (run before Step 5 in §6)

- **Step 9 (per §6)** — test whether SCF writes a `Source` attribution when `acf` keys are updated via REST. Result will be appended to §9 below. If SCF does NOT auto-attach, the user will be re-prompted.
- **Step 10 (per §6)** — test whether a single `POST /wp/v2/posts/{id}` with body `{acf, categories, artist, genre, release_type}` updates **all** taxonomies in one shot, or only `meta`. If only `meta`, an additional `POST /wp/v2/posts/{id}` per taxonomy term set is required.

---

## 5. Endpoint cheat-sheet (verified)

WordPress — `http://10.17.3.3:8085/wp-json/wp/v2`, auth `Basic <base64>`:

| Need | Endpoint | Method | Body | Notes |
| --- | --- | --- | --- | --- |
| List posts to enrich | `/posts?per_page=100&page=N&context=edit` | GET | – | `X-WP-Total`; paginate by short-page detection |
| Update post + ACF + taxonomies | `/posts/{id}` | POST | `{acf:{...}, categories:[...], artist:[...], genre:[...], release_type:[...]}` | Taxonomies are top-level. ACF under `acf`. |
| Upsert term | `/artist` `/genre` `/release_type` | POST | `{name, slug}` | WP returns 400 `term_exists` on dup name — re-fetch by `?slug=` and reuse id |
| Read all categories | `/categories?per_page=100` | GET | – | 7 ids cached at startup |
| Custom post type (optional) | `/album?per_page=100` | GET | – | only if we migrate posts (questions.md Q2) |

Spotify — `https://api.spotify.com/v1`. **Client Credentials Flow only** (Q7 answer: user provides ID+secret in `.env`, no OAuth needed). Token cached in memory, refreshed on `expires_in` expiry.

| Need | Endpoint | Method | Notes |
| --- | --- | --- | --- |
| Get token | `https://accounts.spotify.com/api/token` | POST form-encoded | `grant_type=client_credentials`; header `Authorization: Basic <base64(S_CLIENT_ID:S_CLIENT_SECRET)>`. Response: `{access_token, token_type:"bearer", expires_in:3600}`. |
| Search | `GET /search?q=…&type=album&limit=10&market=US` | GET | field filters `q=album:"…" artist:"…"` only when ladder rung 0 empty |
| Album | `GET /albums/{id}?market=US` | GET | full body incl. `tracks.next` (paged) |
| Album tracks (paginated) | follow `tracks.next` until null | GET | add `limit=50` |
| 429 | – | – | parse `Retry-After`, sleep, retry once |

Last.fm — `https://ws.audioscrobbler.com/2.0`:

| Need | Endpoint | Notes |
| --- | --- | --- |
| Search | `GET /?method=album.search&album=…&api_key=…&limit=10&format=json` | returns `{results.albummatches.album[]}` |
| Detail + tags | `GET /?method=album.getinfo&api_key=…&artist=…&album=…&autocorrect=1&format=json` | **Crucial: read `tags.tag[]`, not `toptags`** (latter is null) |

---

## 6. Work plan

```
Step 1 — Scaffold post_to_album.py:
           argparse, .env loader, WP auth, taxonomy seeding (Artist/Genre/Release Type)
Step 2 — Spotify Client-Credentials token + 429 retry-once + ladder search
Step 3 — Spotify album fetch + paginated tracks
Step 4 — Last.fm album.getinfo (with blocklist filter)
Step 5 — Compose per-post PATCH body (per algorithm in §3), dry-run dump to ./out/
Step 6 — Apply path, 1 req/sec, dump unresolved matches
Step 7 — Subcommand `stats` — ACF + taxonomy fill-rate before/after
Step 8 — Idempotency re-run test on synthetic fully-filled post
Step 9 — Empirical test of SCF `_source` behavior on throwaway post (Q4=i)
Step 10 — Empirical test of whether POST /posts/{id} with body={{acf, taxonomies, categories}} updates taxonomies in one shot (Q14)
```

Each step is a stop point. Steps 9 and 10 are one-time research steps; their outcomes are documented in §9 below and may force minor code changes to steps 5–6.

---

## 7. YAGNI scope

**Not in v1:**

- MusicBrainz / Deezer / Discogs fallbacks (Q6=A: Last.fm only)
- Spotify `album.genres[]` as a source (Q5=III: "Spotify genres are unreliable")
- Custom search ML, only difflib + field filters
- Web UI / TUI
- Backup/restore (WP keeps its own revisions)
- Per-user Spotify data (Client Credentials is enough for catalog)
- Transliteration (Björk vs Bjork)
- Cover image management / alt-text updates (Q13=iii)
- Audio feature extraction (energy, danceability)
- Posts to the new `album` custom post type (Q2=iii)
- Relisten detection / multiple `release_type` terms per post (Q1=a, Q3=A)
- OAuth user-flow (Q7: env-only Client Credentials)
- Spotify `_source` companion writes (Q4=i — depends on Step 9)
- One-shot POST taxonomy updates or sequential (Q14 — depends on Step 10)

---

## 8. Schema summary (live)

For reference, this is the Post-2026-07-05 SCF schema we are targeting. Spotify fills album/track metadata; **Last.fm fills genre + mood** (no Spotify `album.genres[]`); everything besides the skip-list is auto-fillable.

```
group meta:
  repeater music_tracks {disc_number, track_number, title, duration_ms, spotify_id, highlight, explicit}
  number  music_rating          [skip]
  bool    music_favorite        [skip]
  number  music_length_ms       [auto]
  text    spotify_album_id      [auto]
  url     spotify_album_url     [auto]
  date    music_release_date    [auto]
  date    music_listened_at     [auto-if-empty from post.date]
  text    lastfm_release_id     [auto]
  number  music_total_tracks    [auto]
  number  music_avg_track_ms    [auto]
  bool    music_explicit        [auto]
  repeater music_mood_tags {mood}  [auto — Last.fm top 3, may be empty]
  text    music_notes            [skip]
  bool    unreleased             [skip]
  number  listen-count           [auto-if-empty default 1; never overwrite non-1]

custom taxonomies (locked):
  artist       <- from post_tag names (1:1 passthrough, no split)
  genre        <- Last.fm tags only (top 3 after blocklist; Spotify genres ignored)
  release_type <- compute_release_type heuristic output (one term per post)
```

---

## 9. Locked answer decisions (verbatim from `questions.md`)

Every question that block-coding was answered with a sentence-form preference. Below is the canonical, locked-in record of those answers so the implementation can be audited.

| # | Question | Answer (verbatim, paraphrased) | Plan section affected |
| --- | --- | --- | --- |
| Q1 | release-type taxonomy semantics | **(a)** — each post gets exactly one term (`Album` / `EP` / `Single` / `Compilation`). No `[Album, Relisten]`-style multi-term lists. | §2c, §4-F |
| Q2 | new `album` custom post type | Default (iii) — ignore `album` post type. CLI writes only to `post`. Migration is out of scope. | §7, §4 |
| Q3 | how `listen-count` is determined | **(A)** — default `listen-count=1` if empty. Relistens (`≥2`) bumped by hand in WP admin afterwards. | §4-C, §8 |
| Q4 | SCF `_source` companion fields on REST writes | Default (i) — don't write `_source`. **Empirically test on one throwaway post first**, document outcome in §9 (Research results subsection below). | §4-D, §6 step 9 |
| Q5 | genre source & priority | **(III)** — Last.fm only. Spotify `album.genres[]` is **not used** ("Spotify genres are unreliable"). Top 3 with blocklist. | §4-H, §3 algorithm |
| Q6 | alternative genre platforms | **(A)** — Last.fm only. No MusicBrainz / Deezer / Discogs fallback in v1. | §7, §4-H |
| Q7 | Spotify OAuth flow | NVM — user gave `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` in `.env`. CLI uses **Client Credentials Flow**, no OAuth, no `--auth` subcommand. | §3, §4-K, §5 |
| Q8 | albums CPT taxonomy inheritance | Skip (Q2 was answered (iii), so this question is moot). | §7 |
| Q9 | `music_listened_at` auto-fill | **(a)** — auto-fill only if currently empty, value = `post.date[:10]`. The 10 posts already populated with `YYYYMMDD` strings are left alone. Don't transform existing format. | §4-B |
| Q10 | idempotent re-run depth | **(β)** — skip post if EVERY auto-fillable SCF field is non-empty. Otherwise process but never overwrite an already-populated value. | §4-L, §3 algorithm |
| Q11 | Last.fm empty-tags failure mode | **(c)** — leave `music_mood_tags` & `genre` empty, log CLI warning. Don't pollute with stubs or fall back to Spotify genres. | §4-I, §3 algorithm warnings |
| Q12 | what gets logged on failed match | Default (X) — one-line `WARN: post N — no Spotify match for "X" / "Y". See unresolved.json.` per failure. Top-5 candidates stay in `unresolved.json` (don't spam stderr). | §3 unresolved path |
| Q13 | Album Art Picker V2 plugin | Default (iii) — leave running untouched. CLI does not modify title or `featured_media`. No conflict expected. | §4-M, §7 |
| Q14 | WP REST POST/PATCH taxonomies | Empirical test — figure out myself whether one POST updates all taxonomies. Document in §9 (Research results subsection below). | §6 step 10 |
| Q15 | final run-command syntax | Default invocation shape: `--all`/`--limit N`, `--offset M`, mutually-exclusive `--dry-run`/`--apply`, optional `--only-empty-field=…`. No `--post-ids`. No `--category`. | §3 CLI signature |

### Research results (Steps 9 and 10 completed)

| Test | Outcome | Effect on plan |
| --- | --- | --- |
| **Step 9** — SCF `_source` on REST writes | **CONFIRMED**: SCF **auto-attaches** a `<f>_source` companion on every ACF key written via REST.  No code change required — CLI never writes `_source` itself. | None. |
| **Step 10** — POST `/wp/v2/posts/{id}` updates all taxonomies | **CONFIRMED**: a single POST with body `{acf:{...}, categories:[6], artist:[id], genre:[id], release_type:[id]}` updates **all** sections atomically.  No sequential per-taxonomy calls needed. | None. |

Additional empirical findings beyond the original plan (locked-in):

- SCF stores dates in **`d/m/Y`** for both `music_release_date` and `music_listened_at` (`return_format`=`d/m/Y`).  Spotify's raw `YYYY-MM-DD` is accepted and reformatted.  Spotify year-only / month-only `release_date` strings must be coerced to a full date before posting (the CLI falls back to `YYYY-01-01` / `YYYY-MM-01`).
- No empty values may be written to numeric ACF keys — sending `""` for `music_length_ms` etc. yields HTTP 400 `"is not of type number,null"`.  CLI now skips writing numerically-zero/empty values.
- `tags.tag[]` in `album.getinfo` can be returned as a **single object** (not a list) when Last.fm has exactly one tag.  `pick_top_tags` coerces to list defensively.
- `spotify_album.explicit` is reliably **null** at the album level — `music_explicit` MUST be computed from `any(track.explicit is True)` across `tracks.items`, NOT from `album.explicit`.

These research results were appended here after Steps 9–10 completed, before any bulk `--apply` runs.
