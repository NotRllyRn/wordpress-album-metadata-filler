# Plan 1: Canonical Spotify Search and Last.fm Candidate Matching

## Goal

Find the same album on Last.fm that was selected on Spotify, while preserving edition-specific text such as `Deluxe`, `Remastered`, `Single`, `EP`, and `Version`.

The plan deliberately separates three concepts:

1. **Raw value**: the original human-facing string used for API queries and WordPress writes.
2. **Comparison key**: a minimally normalized temporary string used only to compare two values.
3. **Stored value**: the exact canonical Spotify value written to SCF.

No comparison transformation is allowed to change the stored value.

## Official API findings

### Last.fm `album.search`

The official method:

- Requires only an album name.
- Supports `limit` and `page`.
- Returns album matches sorted by Last.fm relevance.
- Does not accept an artist filter.
- Returns lightweight candidate data, not the full metadata and tracklist used by this project.

Consequence: artist matching must happen locally after search results are returned.

### Last.fm `album.getInfo`

The official method:

- Accepts `artist` plus `album`, or an `mbid`.
- Returns metadata and a tracklist.
- May return a MusicBrainz ID, release date, top tags, and tracks.
- Supports autocorrection when querying by artist and album.

Consequence: retain this method after candidate selection. Prefer `mbid` when the chosen search candidate supplies one; otherwise use the candidate's own Last.fm artist and album strings. `LastFM.album_getinfo(...)` must unwrap the response and return the inner `data["album"]` object; a missing or non-object `album` is a malformed provider response, not a successful empty match.

### Last.fm JSON and errors

Official REST documentation states that:

- Repeated XML nodes become JSON arrays.
- API failures in JSON use an `error` and `message` object.
- A successful HTTP status does not by itself prove that the API call succeeded.
- Requests should use an identifiable `User-Agent` and reasonable request volume.

Consequence: centralize Last.fm request parsing and test both HTTP errors and JSON API errors. Keep provider failures (HTTP, timeout, authentication, rate limit, JSON API error, or malformed response) distinct from a successful response containing no candidates. Diagnostics and unresolved records must not label provider failures `no_results`.

### Spotify search

Spotify supports `album` and `artist` search filters. The current search ladder can remain, but its inputs should be raw canonical strings rather than destructively normalized strings.

## Decision: remove destructive normalization

### Remove from Spotify and Last.fm query preparation

Delete behavior that:

- Removes diacritics.
- Removes text such as `remastered`, `deluxe`, `anniversary`, `remix`, `explicit`, or `clean`.
- Removes suffixes such as `- Single`, `- EP`, `- Album`, `- Deluxe`, `- Remastered`, `- Remix`, `- Version`, or `- Edit`.
- Removes punctuation merely to make strings look alike.

These words may distinguish the exact release wanted.

### Keep only harmless preparation for raw query values

For a WordPress rendered title, use:

```python
search_title = html.unescape(title).strip()
```

For an artist name, use:

```python
search_artist = html.unescape(artist).strip()
```

`html.unescape` is needed because WordPress REST rendered strings may contain entities such as `&amp;`. `strip` removes accidental surrounding whitespace. Nothing else should change the API query.

### Add a comparison-only key

Use one small function for all Spotify and Last.fm string comparisons:

```python
def match_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", html.unescape(value or "")).casefold().split())
```

Properties:

- `html.unescape`: treats `A &amp; B` as `A & B`.
- `NFC`: canonical Unicode normalization without compatibility folding.
- `casefold`: robust caseless comparison.
- `split` plus `join`: collapses repeated whitespace.
- It preserves punctuation, accents, edition markers, and suffixes.

Do not use the returned key for API queries or WordPress writes.

## Target Spotify flow

### Step 1: Read raw WordPress identity

```python
wp_title = html.unescape(post["title"]["rendered"]).strip()
wp_artists = [html.unescape(name).strip() for name in tag_names if name.strip()]
```

The existing post title and artist tags are treated as intentional canonical release identity.

### Step 2: Search Spotify with raw values

Keep a small search ladder. Recommended order:

```text
1. album:"<raw title>" artist:"<raw primary artist>"
2. <raw title> <all raw artists>
3. <raw title>
```

Reasons:

- The fielded query expresses the strongest intent first.
- The free-text query helps with collaborations and punctuation differences.
- Title-only search remains a final fallback.

Keep `limit=10`. Do not add pagination until real examples show it is necessary.

### Step 3: Rank Spotify candidates with `match_key`

Retain the current title-plus-artist structure, but compare minimally normalized keys.

Recommended score:

```python
score = 0.65 * title_score + 0.35 * artist_score
```

The exact weights are less important than hard identity gates. Add:

```python
if title_score < 0.80 or artist_score < 0.70:
    reject
```

Then accept only if the combined score meets a tuned threshold, initially `0.82`. Rank candidates by that same combined score; do not add a second tie-breaker. When a runner-up passes the title and artist gates and the winner's score gap is less than `0.05`, reject the result as `spotify_ambiguous`. This initial winner-gap threshold must be tuned from saved examples alongside the existing score thresholds.

If the WordPress post supplies no usable artist names, no Spotify candidate may auto-accept from title similarity alone. Record the post as unresolved for missing artist evidence. The current `0.35` combined threshold is too permissive for an automated metadata writer.

### Step 4: Preserve Spotify's returned values

After selecting and fetching the full Spotify album:

```python
spotify_title = album["name"]
spotify_artists = [a["name"] for a in album.get("artists", [])]
```

Do not normalize `spotify_title` before storing it or before using it as the Last.fm search query.

## Target Last.fm flow

### Step 1: Add a shared request helper

Replace repeated URL construction and response parsing with one private helper:

```python
def _get(self, method: str, **params):
    params |= {"method": method, "api_key": self.api_key, "format": "json"}
    req = urllib.request.Request(
        f"{self.base}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "wordpress-album-metadata-filler/1.0"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=30))
    if data.get("error"):
        raise RuntimeError(f"Last.fm {data['error']}: {data.get('message', 'unknown error')}")
    return data
```

Raise or return a typed failure result; do not collapse errors to `{}` or `[]`. Callers must distinguish provider failure from a valid response with zero matches.

### Step 2: Add `album_search`

```python
def album_search(self, album: str, limit: int = 10) -> list[dict]:
    data = self._get("album.search", album=album, limit=limit)
    matches = data.get("results", {}).get("albummatches", {}).get("album", [])
    return matches if isinstance(matches, list) else [matches] if matches else []
```

Call it with the raw Spotify title:

```python
candidates = lastfm.album_search(album["name"], limit=10)
```

Do not pre-strip edition words.

### Step 3: Score title and artist

Recommended function:

```python
def lastfm_candidate_score(spotify_album: dict, candidate: dict) -> tuple[float, float, float]:
    title_score = similarity(spotify_album["name"], candidate.get("name", ""))
    artist_score = max(
        (similarity(a["name"], candidate.get("artist", "")) for a in spotify_album.get("artists", [])),
        default=0.0,
    )
    return 0.70 * title_score + 0.30 * artist_score, title_score, artist_score
```

Where:

```python
def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, match_key(a), match_key(b)).ratio()
```

Why these fields:

- Title is the search target and includes edition identity.
- Artist disambiguates common album names.
- Search rank alone is not enough because Last.fm cannot filter `album.search` by artist.

### Step 4: Use hard gates, not only a weighted score

Initial constants:

```python
LASTFM_MIN_TITLE = 0.85
LASTFM_MIN_ARTIST = 0.75
LASTFM_MIN_SCORE = 0.85
LASTFM_MAX_TIE_GAP = 0.03
```

Acceptance:

```python
winner = max(scored, key=lambda x: x[0])
runner_up = sorted(scored, reverse=True)[1] if len(scored) > 1 else None

if winner.title < LASTFM_MIN_TITLE or winner.artist < LASTFM_MIN_ARTIST:
    reject
if winner.total < LASTFM_MIN_SCORE:
    reject
if runner_up and winner.total - runner_up.total < LASTFM_MAX_TIE_GAP:
    reject_as_ambiguous
```

Treat these thresholds as starting values. Tune them using saved real examples, not intuition alone.

### Step 5: Add an exact-match fast path

Before fuzzy ranking:

```python
exact = [
    c for c in candidates
    if match_key(c.get("name", "")) == match_key(album["name"])
    and any(match_key(c.get("artist", "")) == match_key(a["name"]) for a in album.get("artists", []))
]
```

If exactly one candidate is exact, choose it. Multiple exact candidates are ambiguous unless exactly one candidate has a nonempty, syntactically usable MBID that is unique within that exact set. In that sole case, the MBID disambiguates the candidate. Otherwise reject as ambiguous: do not use search order and do not fetch an arbitrary candidate. If no Spotify artist exists or no candidate artist matches one, title equality alone cannot auto-accept.

### Step 6: Fetch the selected candidate with `album.getInfo`

Preferred request:

```python
if candidate.get("mbid"):
    info = lastfm.album_getinfo(mbid=candidate["mbid"])
else:
    info = lastfm.album_getinfo(
        artist=candidate["artist"],
        album=candidate["name"],
        autocorrect=0,
    )
```

The current direct call uses Spotify strings and `autocorrect=1`. After search selects a Last.fm candidate, use the candidate's own identity. This prevents autocorrection from silently moving to a different album.

The official `album.search` sample documents an `id` but not an MBID. Real JSON results may or may not expose `mbid`. Code must handle its absence.

### Step 7: Revalidate returned identity

`album_getinfo` returns the inner album object, so `info` below is that object rather than `{ "album": ... }`.

After `getInfo`, compare:

1. Returned album name vs. Spotify album name.
2. Returned artist vs. at least one Spotify album artist.
3. Returned album name and artist vs. the selected Last.fm candidate.

Reject if the returned object no longer passes the title and artist gates.

### Step 8: Use track evidence when available

`album.getInfo` can return a tracklist. Spotify already has a complete tracklist. Use it as optional confirmation, not a mandatory dependency.

Minimal track key:

```python
def track_keys(tracks):
    return [match_key(t.get("name") or t.get("title") or "") for t in tracks]
```

Recommended validation order:

```text
A. If Last.fm has no tracks: keep the title-and-artist match.
B. If track counts are equal: compare positional track names.
C. If counts differ: compare set overlap because editions may add bonus tracks.
D. Reject only when track evidence clearly contradicts the selected identity.
```

Simple overlap:

```python
overlap = len(set(spotify_keys) & set(lastfm_keys)) / max(1, min(len(spotify_keys), len(lastfm_keys)))
```

Initial rule:

```python
if lastfm_keys and overlap < 0.60:
    reject
```

Use a permissive contradiction threshold because Last.fm may merge editions, omit tracks, or use alternate track labels. Do not add a complex track alignment algorithm unless fixtures prove it is needed.

## Best fields to compare

### Required

| Field | Use | Reason |
|---|---|---|
| Album title | Search, score, hard gate | Primary identity and edition text |
| Artist name | Score, hard gate | Disambiguates common titles |

### Optional confirmation

| Field | Use | Limitation |
| --- | --- | --- |
| Track count | Contradiction check | Deluxe and regional editions legitimately differ |
| Track titles | Overlap confirmation | Last.fm can omit or rename tracks |
| Release year/date | Weak tie-breaker only | Last.fm dates can be missing or reflect another edition |
| MBID | Pin selected Last.fm object and store | Spotify does not expose an MBID for direct comparison |

### Do not use for identity

- Listener count.
- Play count.
- Streamable status.
- Image URL.
- Last.fm result order by itself.
- Last.fm page URL by itself.
- Popularity.

These fields describe popularity, availability, or presentation rather than release identity.

## Tag retrieval decision

The official `album.getTopTags` method returns global top album tags ordered by popularity. `album.getTags` is user-specific and is not appropriate here.

Two valid implementations exist:

### Preferred minimal implementation

Continue extracting top tags from the selected `album.getInfo` response, accepting both common JSON keys:

```python
tag_root = info.get("toptags") or info.get("tags") or {}
```

This avoids a third Last.fm request.

### Fallback only if fixtures prove necessary

Call `album.getTopTags` after the candidate is selected, preferably by MBID. Do not add this call preemptively. It increases network usage and failure surface.

## Examples that the new policy handles correctly

| WordPress/Spotify title | Old destructive behavior | New behavior |
| --- | --- | --- |
| `Rumours (Deluxe Edition)` | Could collapse to `rumours` | Searches and matches the deluxe title |
| `The Dark Side of the Moon (50th Anniversary)` | Could discard anniversary identity | Preserves the anniversary marker |
| `Blue - Remastered` | Could collapse to `blue` | Keeps `Remastered` for exact-edition matching |
| `Example - Single` | Could remove `Single` | Keeps the Spotify canonical suffix |
| `Example - EP` | Could remove `EP` | Keeps the Spotify canonical suffix |
| `A &amp; B` from WordPress | May compare as literal entity text | Queries as `A & B` |
| `Beyonce` vs. an accented canonical name | Old code removed accents everywhere | Raw strings stay untouched; comparison uses Unicode normalization and case folding only |
| `Greatest Hits` by Artist A | Search may return many artists | Artist hard gate prevents Artist B from winning |
| Collaboration credited to two Spotify artists | First-artist-only logic may miss it | Candidate artist is compared with every Spotify album artist |
| Two nearly tied Last.fm candidates | Highest result silently wins | Ambiguity gap sends the post to `unresolved.json` |

## Reliability statement

This design is substantially safer than the current direct `getInfo` call because it searches, scores, gates, and validates.

It still cannot guarantee edition-level identity in every case. Last.fm may merge editions under one page, omit MBIDs, omit tracks, or use a canonical album page that does not preserve Spotify's edition distinctions. The program should classify such cases as either:

- Accepted by strong title and artist evidence.
- Accepted with optional track confirmation.
- Rejected as low-confidence or ambiguous.

It should never describe a title-and-artist-only match as cryptographic proof that two catalog records are the exact same release.

## Exact code changes

### Add

- `match_key()`.
- `similarity()` using `match_key()`.
- `LastFM._get()` or equivalent shared parsing.
- `LastFM.album_search()`.
- Flexible `LastFM.album_getinfo(artist=None, album=None, mbid=None, autocorrect=0)`, returning the inner album object.
- `lastfm_candidate_score()`.
- `choose_lastfm_candidate()`.
- `validate_lastfm_info()`.
- Last.fm match diagnostics in `planned.json` and `unresolved.json`.

### Change

- Spotify search inputs to raw unescaped strings.
- Spotify scoring to use comparison keys rather than stripped titles.
- Spotify thresholds from a very permissive single score to title, artist, combined, and winner-gap gates.
- Last.fm flow from direct `getInfo(spotify artist, spotify title)` to search, choose, then getInfo.
- Tag extraction to accept `toptags` and `tags` containers.
- Last.fm request handling to detect JSON API errors and send a User-Agent.

### Remove

- Edition-word removal from `_norm_title`.
- Suffix removal from `_norm_title`.
- Diacritic removal from title and artist query strings.
- Blind Last.fm `autocorrect=1` on the final selected candidate.
- The assumption that any nonempty `album.getInfo` object is the desired release.
- Title-only auto-acceptance when artist evidence is absent.
- Converting provider errors into ordinary no-result responses.

## Research sources

- <https://www.last.fm/api/show/album.search>
- <https://www.last.fm/api/show/album.getInfo>
- <https://www.last.fm/api/show/album.getTopTags>
- <https://www.last.fm/api/show/album.getTags>
- <https://www.last.fm/api/rest>
- <https://www.last.fm/api/intro>
- <https://developer.spotify.com/documentation/web-api/reference/search>
- <https://docs.python.org/3/library/html.html#html.unescape>
- <https://docs.python.org/3/library/unicodedata.html#unicodedata.normalize>
- <https://docs.python.org/3/library/stdtypes.html#str.casefold>
- <https://docs.python.org/3/library/difflib.html#difflib.SequenceMatcher>
