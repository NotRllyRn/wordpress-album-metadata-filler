# Album Art Picker (Spotify) V2 — Analysis

A focused look at how this WordPress plugin (plugin path:
`Album Art Picker (Spotify) V2/`, main file
`spotify-album-art-picker.php`, companion `assets/js/admin.js`)
processes artist tags, post titles, classifies releases, and searches
Spotify — plus recommendations for tightening up matching.

---

## 1. Plugin shape (relevant to this analysis)

- WordPress plugin, single-file PHP class `IAALP_Plugin` plus one main
  JS file (`admin.js`, jQuery-based) and one OCR Web Worker
  (`iaalp-ocr-worker.js`).
- Two user flows feed into the same import path:
  1. **Manual search** — type a term in a meta box, hit “Search”.
  2. **Paste-flow / OCR** — paste a screenshot of a row (e.g. Spotify
     playlist), the worker OCRs the cover + title + artist + date,
     the JS builds a candidate query, retrieves Spotify results,
     fuzzy-matches, and the user confirms.
- Both flows converge on `ajax_spotify_album` (fetch full album
  detail), then `ajax_spotify_import` (write to WordPress).
- Spotify auth: Client-Credentials, token cached in
  transient `iaalp_spotify_access_token`. Image download restricted to
  `i.scdn.co` (SSRF protection).
- Transient cache TTL: `IAALP_CACHE_TTL = 12 * HOUR_IN_SECONDS`
  (search and album fetch). Liked-tracks cache: 1 hour.

---

## 2. How the post **title** is set

### On import (`ajax_spotify_import`, `spotify-album-art-picker.php`)

```
$post_update['post_title'] = $album_name;
…
$update_result = wp_update_post( $post_update, true );
```

- The title is assigned verbatim from the Spotify `album.name` (already
  sanitized on the way in via `sanitize_text_field`). No stripping of
  feature artists, brackets, suffix markers (“- Single”, “(Explicit)”,
  etc.), no Unicode normalization, no diacritic folding.
- If `wp_update_post` fails or the user has hand-edited the title
  during the same save (it detects drift via `updateEditorTitle`),
  the JS layer falls back to copying the title to the clipboard with
  a toast.

### Before it becomes the search query (`buildSearchQueries` in `admin.js`)

```
var title = (fields.title || '').trim();
…
queries.push(title + ' ' + artists.join(' '));
queries.push(album  + ' ' + artists.join(' '));
queries.push(artists[0] + ' ' + title);
queries.push(title);
```

- Just `trim()`. No lowercasing, no punctuation folding, no
  `feat./ft.`/`&`/`and` expansion, no ellipsis stripping on the
  query side.
- `extractFeaturedFromTitle` (used for OCR results) does strip
  `(feat. X)` / `(ft. X)` from the **title**, but its output then
  goes through `cleanArtistsToArray` for tags — only the title path
  feeds the fuzzy search, so Stripparen feature text is removed from
  the title before search but **no further normalization** is done.

### Implications

- “Single (Radio Edit)” versus “Single — Radio Edit” versus
  “Single: Radio Edit” all hit Spotify as different strings.
- “(feat. X)” only disappears from the query when the field came
  from OCR (`extractFeaturedFromTitle`); a manual search with that
  string will mismatch.
- No language-aware Unicode folding: `Björk` vs `Bjork` diverge.

---

## 3. How **artist tags** are produced

Two stages. The JS stage (OCR-driven) is much richer than the PHP
storage stage.

### Stage A — Frontend: clean / split for query & display

In `admin.js`:

```
function normalizeArtistSeparators(s) {
  var text = ' ' + String(s||'') + ' ';
  text = text.replace(/^\s*(?:\(?E\)?|\bE\b|\bEXPLICIT\b)\s*/i, '');  // strip explicit badges
  text = text.replace(/\s+(?:feat(?:uring)?|ft\.?|feat\.?)\s+/ig, ', ');
  text = text.replace(/\s+&\s+|\s+and\s+/ig, ', ');
  return stripEllipsis(text.trim());
}

function cleanArtistsToArray(s, fromTitleFeatured) {
  // ^ after splitting on ","
  // strip leading/trailing parens
  // strip "…"
  // merge any "(feat. …)" extracted from title
  // dedupeArtists (case-insensitive)
}
```

`extractFeaturedFromTitle` also pulls `(feat. …)` / `(ft. …)` content
out of the title into the artist list, then strips that parenthetical.

So artist tag candidates (pre-storage) are:

1. Lowercased, trimmed.
2. “EXPLICIT” / “(E)” / “E” badge dropped.
3. `feat. / ft. / featuring / feat.` replaced with `,`.
4. `&` and `and` (whitespace-padded) replaced with `,`.
5. Split on commas; trim each; strip outer parens.
6. Ellipsis dropped.
7. Deduped case-insensitively.

### Stage B — Backend: store as `post_tag`

In `ajax_spotify_import`:

```
foreach ( $artists as $artist ) {
    $tag = trim( preg_replace( '/,/', '', $artist ) );
    if ( '' === $tag ) continue;
    $tag_term = term_exists( $tag, 'post_tag' );
    if ( ! $tag_term ) $tag_term = wp_insert_term( $tag, 'post_tag' );
    …
    wp_set_post_terms( $post_id, array( $tag_id ), 'post_tag', true );
    $tag_count++;
}
```

Backend processing is **very light**:

- Only strips literal commas (`,` only; no `&`, ` and `, `feat.`).
- No case folding, no diacritic stripping, no explicit-badge strip,
  no paren strip, no feat./ft. handling, no dedupe (Wordpress itself
  will dedupe by slug, but slug = literal text, so “Stromae” and
  “stromae” become two tags).
- The artist list is whatever the JS sends along to `artists[]`
  unmodified — and **only manual-search UI sends Spotify’s artist
  names directly**; the OCR flow passes the cleaned array.

### Notable inconsistency

- OCR flow: full normalization → clean tags.
- Manual flow: passes raw Spotify names (`album.artists[].name`)
  straight in. If someone searches manually for `"Drake & 21 Savage"`,
  the post ends up with a single tag `Drake & 21 Savage &` (whitespace,
  etc.) — definitely wrong.

`&` and ` and ` survive into tags. `feat.` survives too. `(E)` /
“Explicit” badge survives. Diacritics and case both make duplicate
tags possible.

---

## 4. Release-type heuristic

`compute_release_type_from_tracks()` in
`spotify-album-art-picker.php:815-842` is the authoritative
classification.

```
$dur_30m = 1800000  // 30 min
$dur_10m = 600000   // 10 min
$track_count = count($tracks)
$total_ms    = sum of durations
$max_track_ms = longest track

if ( track_count >= 7  ||  total_ms >= dur_30m ) {
    'Album'
}
elseif ( (track_count in [4..6] && total_ms < dur_30m)
      || (track_count in [1..3] && max_track_ms >= dur_10m) ) {
    'EP'
}
elseif ( track_count in [1..3] && total_ms < dur_30m
                                  && max_track_ms < dur_10m ) {
    'Single'
}
```

Then in `ajax_spotify_album`:

```
$default_type = ( 'compilation' === strtolower( $album_type ) )
                ? 'Compilation'
                : $calc['computed_type'];

$compilation_conflict = ( 'compilation' === $album_type )
                        && ( 'Compilation' !== $calc['computed_type'] );
```

So the precedence is:

1. Spotify `album_type === "compilation"` → **Compilation** (always wins).
2. Otherwise the heuristic above.

The four-way table:

| Spotify `album_type`                              | classifier result       |
| ------------------------------------------------- | ----------------------- |
| `album`/`single` and tracks ≥ 7 or ≥ 30 min       | Album                   |
| `album`/`single` and 4–6 tracks                   | EP                      |
| `album`/`single` and 1–3 tracks and one ≥ 10 min  | EP                      |
| `album`/`single` and 1–3 tracks, all < 10 min     | Single                  |
| `compilation`                                     | Compilation (overrides) |

### Observed boundary / quirks

- 1-track EP with a 30+ minute track → falls into the Album bucket
  (`total_ms >= 30m`).
- 6-track EP with 35 min total → still EP
  (the EP clause explicitly requires `total_ms < 30 min`).
- 7-track 25-minute release → Album by track-count, even though many
  labels would call that an EP.
- 4-track 31-minute EP → Album by total duration.
- Compilation can violate all of the above and still be labelled
  Compilation (correct).
- UI side adds manual override buttons
  (`.iaalp-choose-type[data-type="…"]`) so the user can re-tag
  before saving.

The heuristic ignores Spotify’s `album_group` (`album` / `single` /
`compilation` / `appears_on`). The plugin uses only `album_type`
which is a narrower value (`album`, `single`, `compilation`).

---

## 5. How the plugin interacts with Spotify

Two distinct surfaces: **search** (fuzzy/direct) and **album fetch**
(precise by ID or href).

### 5a. Search endpoint

`ajax_spotify_search` → `GET https://api.spotify.com/v1/search`
with query params:

| param   | value                                |
| ------- | ------------------------------------ |
| `q`     | `term` from POST (sanitized text)    |
| `type`  | `album` (default) or `track`         |
| `limit` | clamped to `[1, 30]` (default 15)    |

Result post-processing:

- **album** mode → each item run through `reduce_album_list_item`,
  exposing `{id, name, images, artists[], external_urls.spotify,
  href, album_type}`.
- **track** mode → each item gives `{track: {id, name}, album:
  <reduced>}` so the UI can show the album the track belongs to.

Transient cache key:

```
iaalp_sp_search_ + md5( mb_strtolower(term) + '|' + mode + '|' + limit )
```

TTL: 12 hours.

### 5b. Album fetch endpoint

`ajax_spotify_album` → `GET https://api.spotify.com/v1/albums/<id>`.

- Accepts either `album_id` or `album_href`.
- Paginates `tracks.next` up to 10 follow-on calls.
- Returns `{id, name, album_type, images[], external_urls.spotify,
  artists[], tracks.items[],
  computed_type, default_type, track_count, total_ms, max_track_ms,
  compilation_conflict, compilation_note}`.
- Transient cache key: `iaalp_sp_album_ + md5(id)`.

### 5c. Match (paste / OCR) flow

1. **Build query ladder** (in order, dedup case-insensitively):

   ```
   [ "title artists[…]",
     "album artists[…]",
     "artists[0] title",
     "title" ]
   ```

   `album` is normally left blank for OCR-of-Spotify-row (no album
   field), so this ladder collapses to:

   ```
   [ "title + ' ' + artists.join(' ')",
     "artists[0] + ' ' + title",
     "title" ]
   ```

2. Try each query sequentially via the backend
   (`iaalp_spotify_search`, album mode, limit 15) until the first
   non-empty result.

3. **Fuse.js fuzzy re-rank** of the returned candidates:

   ```
   new Fuse(results, {
     includeScore: true,
     threshold: 0.45,
     keys: [ { name: 'name',       weight: 0.6  },
             { name: 'artistsStr', weight: 0.35 } ]
   });
   fuse.search( title + ' ' + artistsStr );
   // fallback: fuse.search(title) if first returned empty
   ```

   `textScore = 1 - min(rawScore / 0.45, 1)`. Higher is better.

4. **Image similarity** for the top-5:

   - Take the cover image (large).
   - Perceptual hash it to 64-bit dHash (worker).
   - Compare to a precomputed hash of the user-supplied crop cover
     (Hamming distance over 64 bits → normalised similarity).

5. **Combined score**:

   ```
   combinedScore = 0.75 * textScore + 0.25 * imageScore
   ```

   Top 5 sorted by combined, top 1 auto-selected, top 3 shown in a
   tray.

### 5d. Token & error handling (`spotify_request`)

- Bearer token from `get_spotify_access_token(false)`; reused until
  401, then refresh-and-retry once.
- Detects 429s, parses `Retry-After`, returns a `rate_limited` WP
  error with `retry_after` data so the JS can defer retries.
- HTTP errors flow back as `WP_Error`; JS unwraps `(resp && resp.data
  && resp.data.message)`.

### 5e. The “find this in my Liked Songs” side feature

- `ajax_spotify_liked_date` paginates `GET /v1/me/tracks` up to
  ~25 000 tracks (cached 1 h).
- For every liked track: compute normalized name, album name, every
  artist name.
- For the candidate album: same normalization, plus normalized track
  names.
- Match rule per liked track:
  - liked track name ∈ album track names (normalized exact), AND
  - liked album name === candidate album name (normalized exact), AND
  - at least one liked artist ∈ candidate artists.
- Among matching `added_at` ISO strings, sort lexicographically and
  take the latest; the YYYY-MM-DD is shipped back. This becomes the
  suggested WordPress post date.

The matching here is **double-exact post-normalization** — there is *no*
fuzzy fallback. If the user liked a track under one album name and
Spotify later renamed the album, no date will be returned.

---

## 6. Recommendations — improving match accuracy from

**(release-type, processed-artists, processed-title)**

These are concrete, narrowly-scoped improvements that target the three
inputs you already have. They are ordered roughly by impact-per-effort.

### A. Use Spotify search field filters

Spotify’s `q=` supports `artist:`, `album:`, `track:`, `year:`,
`tag:`, `upc:` etc. The plugin currently passes only a freeform bag.

Recommended query ladder (build on, do not replace, the existing one):

1. **`album:"<title>" artist:"<primary>"`** — preferred if you have
   at least one artist.
2. **`album:"<title>" year:<YYYY>-<YYYY>`** — when OCR gave a date.
3. `album:<title> <artist>` (no quotes; looser).
4. `<title> <artist>` (current fallback ladder).

Notes:

- Strip diacritics before quoting (see C).
- Strip trailing “(Explicit)” / “(Deluxe)” / “ - Single” before
  quoting — they trigger Spotify returning different releases even
  with quoted field searches.
- If `chosen_type` (Album/EP/Single/Compilation) is known, drop
  items whose `album_type` conflicts before sending the ladder.
  Map: `Album → album`, `Single → single`, `Compilation →
  compilation`, `EP → album` (Spotify has no native EP; treat as
  album and accept minor mismatch).

### B. Use Spotify `include_groups` only via album fetch — or fall back to artist-albums

When you have a primary artist ID, the more reliable path than
search is:

```
GET /v1/artists/{id}/albums?include_groups=album,single&country=US&limit=50
```

…and then locally score each candidate by exact / fuzzy title + track
count + total_ms being within tolerance of the user’s inputs. This
sidesteps free-text search ranking issues (popularity bias,
transliteration, deluxe editions, etc.).

The plugin already pulls full album data — so the small JS change is
to accept an artist-id input (derivable from the first hit of an
initial free-text search, then re-fetch the catalog).

### C. Upgrade the **title** normalisation (used in both query & compare)

Mirror the artist logic against titles:

```
function normalizeTitleForMatch(s) {
  return (s || '')
    .toLowerCase()
    // Diacritic folding for query
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    // Bracket qualifiers that almost never matter
    .replace(/\(\s*(explicit|clean|remaster(ed)?|deluxe|anniversary|special edition|mono|stereo|remix)\s*\)/gi, '')
    .replace(/\[\s*(explicit|clean|remaster(ed)?|deluxe|anniversary|special edition)\s*\]/gi, '')
    // Common format suffixes
    .replace(/\s*[-–—]\s*(single|ep|album|deluxe|remaster(ed)?|remix|version|edit)(.*)$/i, '')
    // The remaining "(feat. X)" is now stripped before (extractFeaturedFromTitle)
    .replace(/[·•]/g, ' ')
    .replace(/[‐-―—]/g, '-')           // unify dashes
    .replace(/\s+/g, ' ')
    .trim();
}
```

Plugin-side (`normalize_string`) only lowercases + trims + collapses
whitespace. Adding the diacritic fold and quality-tag strip here
would benefit every comparison (search side, like-date side, future
match side).

### D. Upgrade **artist tag & query** normalisation

Apply `normalizeArtistSeparators` + `cleanArtistsToArray` in PHP at
import time, not only on the OCR JS path. At minimum (writing on
top of the current trim/comma-strip):

```
$tag = trim( $artist );
$tag = preg_replace( '/\s+&\s+|\s+and\s+/i', ', ', $tag );
$tag = preg_replace( '/\s*(?:feat\.?|ft\.?|featuring)\.?\s+/i', ', ', $tag );
$tag = preg_replace( '/\s*\((?:E|EXPLICIT)\)\s*/i', '', $tag );
$tag = preg_replace( '/[\(\)\.…]/u', '', $tag );        // strip remaining parens, ellipsis
$tag = remove_diacritics( $tag );                       // optional but recommended
$tag = preg_replace( '/\s+/', ' ', $tag );
```

…then store, **after** splitting on commas+`/`. The current
single-artist-with-no-comma path leaves `"Drake" + "21 Savage"`
in a tag named `"Drake & 21 Savage"`.

For consistency between manual-search and OCR paths, send the same
cleaned array from `admin.js` regardless of source.

### E. Use **release type** as an explicit filter on the candidate set

After Spotify returns candidates:

- If `chosen_type` is known (`Album`/`EP`/`Single`/`Compilation`):
  filter Spotify results so that:

  | user chose | Spotify `album_type` allowed |
  | ---------- | ---------------------------- |
  | Album      | `album` (allow `single` if heuristic says so, otherwise drop) |
  | EP         | `album` with heuristic “EP-compatible” track count (1–6 and total < 30 min) |
  | Single     | `single` (allow `album` if it’s 1–3 tracks each < 10 min) |
  | Compilation| `compilation` only |

- Compare against the heuristic **recomputed** from the candidate
  album’s tracks. If heuristic disagrees with user-chosen type,
  lower confidence, surface a UI warning (the plugin already
  surfaces a note for `compilation` cases; do the same for `EP`
  mismatches).

### F. Enrich the **fuzzy match** with the three inputs

Three small improvements that all hit the existing Fuse
infrastructure:

1. Build a richer pattern:

   ```
   pattern = (processed_title) + ' ' + (processed_artists.join(' '))
   ```

   after running `normalizeTitleForMatch`, `removeDiacritics`, and the
   new tag splitter — both sides.

2. Add artist-first pattern as a tiebreaker when scores are close
   (already exists in `buildSearchQueries`, but currently it’s a
   separate query, not a Fuse re-rank key). Promote to a dedicated
   Fuse key `primaryArtist` so the score reflects both ordering
   choices:

   ```
   keys: [ { name: 'name',            weight: 0.55 },
           { name: 'artistsStr',      weight: 0.30 },
           { name: 'primaryArtist',   weight: 0.15 } ]
   ```

   Populate `primaryArtist` = the first artist name.

3. Use the **chosen_type** as a contextual weight: when the user has
   already selected `Single`, shrink the `name` weight slightly
   (singles are commonly called “X - Single” etc., so loose match is
   common), but penalise deluxe/long releases.

### G. Cross-validate candidates with tracklist + year

When the user OCRs more than cover+title+artist+date, additional
columns (album name, tracklist row, year) can be cross-checked
server-side before the JS shows them:

- Hit `/v1/albums/{id}` for the top N candidates.
- Compare `release_date` to the OCR date (±1 year is fine).
- Compare total duration (`sum(track.duration_ms)`) to OCR’s total if
  available.
- If OCR captured track titles: do a Jaccard-style overlap between
  the candidate album’s tracks and the OCR tracklist; require ≥ 60%
  match for confidence ≥ “acceptable”.

This consumes extra API quota but the plugin already paginates a single
album in `ajax_spotify_album`; expand it into a bulk fetch for the
top 5 candidates.

### H. Patch the liked-date matcher to be **fuzzy**

`find_matching_liked_date` requires exact-normalized-equality on the
album name and exact track-name membership. Two useful relaxations:

- Album-name: allow Levenshtein distance ≤ 2 over the normalized
  strings, OR allow prefix match if the prefix length is ≥ 70% of
  the shorter string (handles “Deluxe Edition” vs base release).
- Track-name: switch from `in_array(..., strict true)` to
  `similar_text ≥ 85%`.

This is the single change that most reduces “but I liked this two
years ago, why didn’t it find a date?” complaints.

### I. Output / display

- Show the chosen release-type in the toast when title+tags are set
  (“Title set, Category set (Single), Tags added (3)”). Currently
  the success message lists `Title/Category/Tags count` but not
  *which* category, which is what someone scanning the post needs.
  (Backend already does $chosen_type in response; the JS toast
  template just doesn’t surface it.)

- Add a small “type-mismatch warning” UI element that appears when
  the OCR/heuristic and the user override disagree. The hint is
  already on the album payload (`compilation_note`); reuse it.

---

## 7. Quick summary

- **Title**: assigned verbatim from Spotify; query side trims only.
- **Artist tags**: normalise aggressively on the OCR/JS side, leave
  raw on the manual/import side. Only literal commas are stripped.
- **Type heuristic**: simple threshold on `track_count` ∈ {1..3},
  {4..6}, ≥7 plus `total_ms` ≥ 30 min and `max_track_ms` ≥ 10 min;
  `album_type === compilation` hard-overrides everything.
- **Spotify search**: free-text, two-mode (`album`/`track`),
  limit 1–30, server-cached 12 h; sequential query ladder on the
  paste flow.
- **Matching fuse**: Fuse.js with `name`/`artistsStr`, threshold
  0.45, plus image dHash for top 5, weighted 0.75/0.25. No Spotify
  field filters, no artist-albums catalog walk, no track-list overlap.

The biggest wins on match accuracy using the three inputs you already
have are:

1. Normalise titles (diacritics, brackets, suffixes) — both query
   and compare.
2. Normalise artists consistently on every path (PHP should match
   what JS already does on OCR paths).
3. Use Spotify’s `q=` field filters (`album:"…" artist:"…"`), and on
   direct album fetches, filter candidates by chosen type vs
   Spotify’s `album_type`.
4. Recompute the release-type heuristic per candidate and surface
   mismatches; downgrade combined score when type disagrees.
5. Use Spotify `release_date` ± OCR date as a tiebreaker.
