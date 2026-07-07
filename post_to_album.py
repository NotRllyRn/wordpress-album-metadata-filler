#!/usr/bin/env python3
"""post_to_album — verbose Python CLI that backfills SCF metadata + taxonomies
on every WordPress post, sourcing data from Spotify (album + tracks) and
Last.fm (genre/mood tags only).

Stdlib only. Single file. See plan.md for the locked-in design.
"""

from __future__ import annotations

import argparse
import base64
import difflib
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

log = logging.getLogger("post_to_album")

# --------------------------------------------------------------------------- #
# Constants — single source of truth for the field map.
# --------------------------------------------------------------------------- #

# Auto-fillable SCF fields. Order matters only for cosmetic output.
AUTO_FILLABLE_FIELDS = (
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
    "music_mood_tags",
    "listen-count",
)

# Category id map for the legacy WP category twin of release_type taxonomy.
CATEGORY_MAP = {
    "Album": 6,
    "EP": 7,
    "Single": 5,
    "Compilation": 98,
}

# Last.fm blocklist (regex patterns).  r"^\w+\s*$" was dropped — it would
# strip real single-word genres like "rock" / "pop" / "ambient".
LFM_BLOCKLIST = (
    r"^\d{4}$",            # year-only tags ("2024")
    r"^aoty$",             # "album of the year"
    r"^best of \d{4}$",    # "best of 2024"
    r"^seen live$",
    r"^favorites?$",
    r"^under \d+$",        # under-2000 listeners / plays
)

# --------------------------------------------------------------------------- #
# Tiny utilities
# --------------------------------------------------------------------------- #

def _strip_diacritics(s: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm_title(s: str) -> str:
    import html as _html
    s = _html.unescape(s or "").lower()
    s = _strip_diacritics(s)
    s = re.sub(r"\(\s*(explicit|clean|remaster(ed)?|deluxe|anniversary|special edition|mono|stereo|remix)\s*\)", "", s)
    s = re.sub(r"\[\s*(explicit|clean|remaster(ed)?|deluxe|anniversary|special edition)\s*\]", "", s)
    s = re.sub(r"\s*[-–—]\s*(single|ep|album|deluxe|remaster(ed)?|remix|version|edit)(.*)$", "", s)
    s = re.sub(r"[·•]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_artist(s: str) -> str:
    return _strip_diacritics((s or "").strip()).lower()


def _post_dmy(date_iso: str) -> str:
    """Convert WordPress ISO `date` (YYYY-MM-DDThh:mm:ss) to SCF's d/m/Y.

    Also accepts Spotify `release_date` precision variants:
      'YYYY', 'YYYY-MM', 'YYYY-MM-DD' — we coerce to a full date.
    Returns '' on unparseable input so the caller can skip the write.
    """
    if not date_iso:
        return ""
    s = (date_iso or "").strip()
    try:
        if len(s) == 4 and s.isdigit():                # "2026"  → year only
            d = datetime(int(s), 1, 1)
        elif len(s) == 7 and re.fullmatch(r"\d{4}-\d{2}", s):  # "2026-05"
            d = datetime.strptime(s + "-01", "%Y-%m-%d")
        else:                                           # full ISO or YYYY-MM-DD
            d = datetime.fromisoformat(s.replace("Z", "+00:00").replace("T", " ") if "T" in s else s)
        return d.strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return ""


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

# --------------------------------------------------------------------------- #
# Release-type heuristic (locked-in per plugin + tracker analysis)
# --------------------------------------------------------------------------- #

def compute_release_type(tracks: list[dict], raw_spotify_type: str) -> str:
    if (raw_spotify_type or "").lower() == "compilation":
        return "Compilation"
    n = len(tracks)
    total = sum(t.get("duration_ms", 0) for t in tracks)
    max_t = max((t.get("duration_ms", 0) for t in tracks), default=0)
    if n >= 7 or total >= 1_800_000:
        return "Album"
    if (4 <= n <= 6 and total < 1_800_000) or (1 <= n <= 3 and max_t >= 600_000):
        return "EP"
    if 1 <= n <= 3 and total < 1_800_000 and max_t < 600_000:
        return "Single"
    return "Album"


# --------------------------------------------------------------------------- #
# SPOTIFY  —  Client Credentials Flow
# --------------------------------------------------------------------------- #

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API       = "https://api.spotify.com/v1"


class Spotify:
    def __init__(self, client_id: str, client_secret: str):
        self._id = client_id
        self._secret = client_secret
        self._tok: str = ""
        self._exp: float = 0.0

    def _ensure_token(self) -> str:
        if self._tok and time.time() < self._exp - 60:
            return self._tok
        basic = base64.b64encode(f"{self._id}:{self._secret}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        req = urllib.request.Request(
            SPOTIFY_TOKEN_URL, data=body, method="POST",
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read())
        self._tok = j["access_token"]
        self._exp = time.time() + float(j.get("expires_in", 3600))
        return self._tok

    def _get(self, url: str) -> Any:
        for attempt in (0, 1):
            tok = self._ensure_token()
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    self._exp = 0  # force refresh
                    continue
                if exc.code == 429 and attempt == 0:
                    retry = int(exc.headers.get("Retry-After", "2"))
                    log.warning("Spotify 429, sleeping %ds", retry)
                    time.sleep(retry)
                    continue
                raise
        raise RuntimeError("unreachable")

    def search_albums(self, q: str, limit: int = 10) -> list[dict]:
        url = f"{SPOTIFY_API}/search?q={urllib.parse.quote(q)}&type=album&limit={limit}&market=US"
        return self._get(url).get("albums", {}).get("items", [])

    def album(self, aid: str) -> dict:
        return self._get(f"{SPOTIFY_API}/albums/{urllib.parse.quote(aid)}?market=US")

    def all_tracks(self, aid: str) -> list[dict]:
        """Follow tracks.next until exhausted."""
        out: list[dict] = []
        url = f"{SPOTIFY_API}/albums/{urllib.parse.quote(aid)}/tracks?limit=50&market=US"
        while url:
            j = self._get(url)
            out.extend(j.get("items", []))
            url = j.get("next")
        return out


# --------------------------------------------------------------------------- #
# Candidate ranking
# --------------------------------------------------------------------------- #

def _score(cand: dict, q_title: str, q_artists: list[str]) -> float:
    c_title = _norm_title(cand.get("name", ""))
    c_artists = [_norm_artist(a.get("name", "")) for a in cand.get("artists", [])]
    title_sim = difflib.SequenceMatcher(a=q_title, b=c_title).ratio() if q_title else 0.0
    artist_sim = 0.0
    if q_artists and c_artists:
        # best match among c_artists for q_artists[0] (primary tag)
        primary = q_artists[0]
        artist_sim = max((difflib.SequenceMatcher(a=primary, b=ca).ratio() for ca in c_artists), default=0.0)
    return 0.6 * title_sim + 0.4 * artist_sim


def search_ladder(spt: Spotify, q_title: str, q_artists: list[str]) -> list[dict]:
    """Three-rung ladder: free-text → quoted-field-and-artist → title-only."""
    free  = " ".join([q_title] + q_artists)
    quoted = f'album:"{q_title}"' + (f' artist:"{q_artists[0]}"' if q_artists else "")
    seen: "OrderedDict[str, dict]" = OrderedDict()
    for q in (free, quoted, q_title):
        if not q or not q.strip():
            continue
        try:
            for c in spt.search_albums(q, limit=10):
                seen.setdefault(c["id"], c)
        except urllib.error.HTTPError as exc:
            log.warning("Spotify search `%s` -> HTTP %d", q[:60], exc.code)
    return list(seen.values())


def best_candidate(spt_results: list[dict], q_title: str, q_artists: list[str]) -> dict | None:
    if not spt_results:
        return None
    scored = sorted(
        ((_score(c, q_title, q_artists), c) for c in spt_results),
        key=lambda x: x[0], reverse=True,
    )
    return scored[0][1] if scored[0][0] >= 0.35 else None


# --------------------------------------------------------------------------- #
# LAST.FM
# --------------------------------------------------------------------------- #

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"


class LastFM:
    def __init__(self, api_key: str):
        self._key = api_key

    def album_getinfo(self, artist: str, album: str) -> dict:
        params = {"method": "album.getinfo", "format": "json",
                  "api_key": self._key, "artist": artist, "album": album, "autocorrect": "1"}
        url = f"{LASTFM_BASE}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read()).get("album") or {}
        except urllib.error.HTTPError as exc:
            log.warning("Last.fm getinfo failed for %s - %s : HTTP %d", artist, album, exc.code)
            return {}


def pick_top_tags(album_info: dict, max_n: int, blocklist: Iterable[str]) -> list[str]:
    """Handles four return shapes:
         ''                       (no tags)
         {'tag': []}              (no tags)
         {'tag': [{name:'…'}]}    (multiple tags, list of dicts)
         {'tag': {name:'…'}}      (single tag — dict, not list!)
    Last.fm may also return tags as bare strings.
    """
    tags = (album_info or {}).get("tags", "") or {}
    raw = tags.get("tag", []) if isinstance(tags, dict) else []
    if isinstance(raw, dict):
        raw = [raw]
    elif not isinstance(raw, list):
        raw = []
    pats = [re.compile(p) for p in blocklist]
    out: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            name = entry
        elif isinstance(entry, dict):
            name = entry.get("name") or ""
        else:
            continue
        name = name.strip()
        if not name:
            continue
        if any(p.match(name) for p in pats):
            continue
        out.append(name)
        if len(out) >= max_n:
            break
    return out


# --------------------------------------------------------------------------- #
# WordPress REST client
# --------------------------------------------------------------------------- #

class WordPress:
    def __init__(self, base: str, user: str, app_pw: str):
        self.base      = base.rstrip("/")
        self.api       = f"{self.base}/wp-json/wp/v2"
        self._auth     = "Basic " + base64.b64encode(f"{user}:{app_pw}".encode()).decode()
        self._hdr_json = {"Authorization": self._auth, "Accept": "application/json",
                          "Content-Type": "application/json"}
        self._hdr_get  = {"Authorization": self._auth, "Accept": "application/json"}

    def _url(self, path: str, **qs) -> str:
        sep = "&" if "?" in path else "?"
        return f"{self.api}{path}" + (sep + urllib.parse.urlencode(qs) if qs else "")

    def _req_get(self, url: str) -> tuple[Any, dict]:
        req = urllib.request.Request(url, headers=self._hdr_get)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()), dict(r.headers)

    def _req_post(self, url: str, body: dict) -> Any:
        for attempt in (0, 1):
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers=self._hdr_json, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt == 0:
                    retry = int(exc.headers.get("Retry-After", "2"))
                    log.warning("WP 429, sleeping %ds", retry)
                    time.sleep(retry)
                    continue
                raise

    # ---- reads ----

    def list_posts(self, per_page: int = 100) -> Iterable[dict]:
        page = 1
        while True:
            url = self._url("/posts", per_page=per_page, page=page, context="edit")
            chunk, hdrs = self._req_get(url)
            if not chunk:
                return
            for p in chunk:
                yield p
            total_pages_hdr = hdrs.get("X-WP-TotalPages", str(page))
            try:
                total_pages = int(total_pages_hdr)
            except (TypeError, ValueError):
                total_pages = page
            if page >= total_pages:
                return
            page += 1

    def total_posts(self) -> int:
        url = self._url("/posts", per_page=1, context="edit")
        req = urllib.request.Request(url, headers=self._hdr_get)
        with urllib.request.urlopen(req, timeout=30) as r:
            try:
                return int(r.headers.get("X-WP-Total", "0"))
            except (TypeError, ValueError):
                return 0

    def list_tax_terms(self, tax: str) -> dict[str, int]:
        """slug → id cache."""
        url = self._url(f"/{tax}", per_page=100)
        try:
            rows, _ = self._req_get(url)
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404):
                rows = []
            else:
                raise
        return {r["name"]: r["id"] for r in rows}

    def list_tags(self, name_to_id: dict[int, str]) -> dict[int, str]:
        url = self._url("/tags", per_page=100, page=1)
        page = 1
        all_ids: list[int] = []
        while True:
            u = self._url("/tags", per_page=100, page=page)
            rows, hdrs = self._req_get(u)
            if not rows:
                break
            all_ids.extend(r["id"] for r in rows)
            tp_hdr = hdrs.get("X-WP-TotalPages", str(page))
            try:
                tp = int(tp_hdr)
            except (TypeError, ValueError):
                tp = page
            if page >= tp:
                break
            page += 1
        for tid in all_ids:
            try:
                tr, _ = self._req_get(self._url(f"/tags/{tid}"))
                name_to_id[tid] = tr.get("name", "")
            except urllib.error.HTTPError as _tag_err:
                log.debug("tag %d lookup failed: %s", tid, _tag_err)
        return name_to_id

    def create_term(self, tax: str, name: str) -> int | None:
        try:
            t = self._req_post(self._url(f"/{tax}"), {"name": name})
            return t["id"]
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 400 and "term_exists" in body:
                # re-fetch by slug
                slug = urllib.parse.quote(re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
                try:
                    rows, _ = self._req_get(self._url(f"/{tax}", slug=slug))
                    if rows:
                        return rows[0]["id"]
                except urllib.error.HTTPError as _slug_err:
                    log.debug("slug re-lookup failed: %s", _slug_err)
                    pass
                # fallback: list all and find by name
                all_rows, _ = self._req_get(self._url(f"/{tax}", per_page=100))
                for r in all_rows:
                    if r["name"] == name:
                        return r["id"]
            log.warning("create_term %s/%s failed: HTTP %d %s",
                        tax, name, exc.code, body[:200])
            return None

    def update_post(self, pid: int, body: dict) -> dict:
        url = self._url(f"/posts/{pid}")
        return self._req_post(url, body)


# --------------------------------------------------------------------------- #
# Field-presence predicate + builder
# --------------------------------------------------------------------------- #

def is_field_present(field: str, v: Any) -> bool:
    """Plan says never overwrite anything currently populated. Treat:
        None, '', 0 (numeric placeholders), False (bool default),
        []  (empty list), {}  as EMPTY → safe to write.
        Music_listened_at 'YYYYMMDD' (no dashes) is treated as PRESENT
        (per Q9=a) — leave alone.
    """
    if v is None:
        return False
    if field in ("music_explicit", "music_favorite", "unreleased"):
        return bool(v)
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        if v == "":
            return False
        if field == "music_listened_at" and re.fullmatch(r"\d{8}", v):
            return True   # honor Q9=a: the legacy YYYYMMDD strings stay
        return True
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return bool(v)


def is_fully_filled(acf: dict) -> bool:
    return all(is_field_present(f, acf.get(f)) for f in AUTO_FILLABLE_FIELDS)


def _set_if_empty(acf_in: dict, acf_out: dict, key: str, value: Any) -> None:
    """Write `value` only if the existing value is empty AND the new value
    is itself non-empty (so we never POST a '0' / '' / False / [] as filler)."""
    if is_field_present(key, value) and not is_field_present(key, acf_in.get(key)):
        acf_out[key] = value


# --------------------------------------------------------------------------- #
# Per-post enrichment
# --------------------------------------------------------------------------- #

def enrich(post: dict, spt: Spotify, lfm: LastFM,
           tag_id_to_name: dict[int, str],
           tax_term_cache: dict[str, dict[str, int]],
           wp: WordPress) -> dict | None:
    """Return a PATCH body for the post or None when no work is needed."""
    pid  = post["id"]
    acf_in = post.get("acf") or {}
    title = post["title"]["rendered"]
    post_date = post["date"]
    tag_names = [tag_id_to_name.get(t, "") for t in post.get("tags", []) if t in tag_id_to_name]

    if is_fully_filled(acf_in):
        log.debug("SKIP post %d '%s' (fully filled)", pid, title)
        return None

    q_title = _norm_title(title)
    q_artists = [_norm_artist(a) for a in tag_names]
    log.debug("post %d :: title=%r artists=%r date=%s",
              pid, title, tag_names, post_date)

    cands = search_ladder(spt, q_title, q_artists)
    if not cands:
        log.warning("post %d -- no Spotify match for %r / %s. See unresolved.json.",
                    pid, title, tag_names[:3])
        return {"__unresolved__": True, "candidates": []}
    winner = best_candidate(cands, q_title, q_artists)
    if winner is None:
        log.warning("post %d -- no good Spotify match for %r / %s. See unresolved.json.",
                    pid, title, tag_names[:3])
        return {"__unresolved__": True, "candidates": cands[:5]}

    log.info("post %d '%s' -> Spotify %s '%s' (%d tracks)",
             pid, title, winner["id"], winner["name"], winner.get("total_tracks", 0))

    album   = spt.album(winner["id"])
    tracks  = spt.all_tracks(winner["id"])

    primary_artist = (album.get("artists") or [{}])[0].get("name") or tag_names[0]
    info          = lfm.album_getinfo(primary_artist, album["name"])
    lfm_tags      = pick_top_tags(info, max_n=3, blocklist=LFM_BLOCKLIST)
    if not lfm_tags:
        log.warning("post %d — no useful Last.fm tags for %s; leaving mood empty",
                    pid, album["name"])

    track_rows = [
        {"disc_number":  t.get("disc_number", 1),
         "track_number": t.get("track_number", 0),
         "title":        t["name"],
         "duration_ms":  t["duration_ms"],
         "spotify_id":   t["id"],
         "highlight":    False,
         "explicit":     bool(t.get("explicit", False))}
        for t in tracks
    ]
    length_ms = sum(t["duration_ms"] for t in track_rows)
    total     = album.get("total_tracks") or len(track_rows)
    rt_term_name = compute_release_type(track_rows, album.get("album_type", ""))
    rt_term_slug = rt_term_name.lower()

    acf_out: dict[str, Any] = {}
    if track_rows or not is_field_present("music_tracks", acf_in.get("music_tracks")):
        _set_if_empty(acf_in, acf_out, "music_tracks", track_rows if track_rows else None)
        if not track_rows:
            acf_out.pop("music_tracks", None)
    _set_if_empty(acf_in, acf_out, "music_length_ms",     length_ms)
    _set_if_empty(acf_in, acf_out, "spotify_album_id",    album["id"])
    _set_if_empty(acf_in, acf_out, "spotify_album_url",   f"https://open.spotify.com/album/{album['id']}")
    _set_if_empty(acf_in, acf_out, "music_release_date", _post_dmy(album.get("release_date", "")))
    _set_if_empty(acf_in, acf_out, "music_listened_at",  _post_dmy(post_date))
    _set_if_empty(acf_in, acf_out, "lastfm_release_id",   (info.get("mbid") or ""))
    _set_if_empty(acf_in, acf_out, "music_total_tracks", total)
    _set_if_empty(acf_in, acf_out, "music_avg_track_ms",
                  (length_ms // total) if total else 0)
    _set_if_empty(acf_in, acf_out, "music_explicit",     any(t["explicit"] for t in track_rows))
    if lfm_tags:
        _set_if_empty(acf_in, acf_out, "music_mood_tags", [{"mood": t} for t in lfm_tags])
    else:
        # leave absent from body — POST/PATCH semantics replace what's given,
        # so omitting means "do not touch this field". Mood stays empty.
        pass
    _set_if_empty(acf_in, acf_out, "listen-count", 1)

    # Term resolution (find-or-create)
    artist_ids = [_ensure_term(wp, tax_term_cache, "artist",     name) or 0 for name in tag_names]
    artist_ids = [i for i in artist_ids if i]

    # genre only if LFM yielded something
    if lfm_tags:
        genre_ids = [_ensure_term(wp, tax_term_cache, "genre", t) or 0 for t in lfm_tags]
        genre_ids = [i for i in genre_ids if i]
    else:
        genre_ids = []

    rt_id = _ensure_term(wp, tax_term_cache, "release_type", rt_term_name) or 0
    cat_id = CATEGORY_MAP.get(rt_term_name)

    body: dict[str, Any] = {}
    if acf_out:
        body["acf"] = acf_out
    if cat_id:
        body["categories"] = [cat_id]
    if artist_ids:
        body["artist"] = artist_ids
    if genre_ids:
        body["genre"] = genre_ids
    if rt_id:
        body["release_type"] = [rt_id]

    return body


def _ensure_term(wp: WordPress, cache: dict[str, dict[str, int]],
                 tax: str, name: str) -> int | None:
    if not name:
        return None
    cache.setdefault(tax, {})
    if name in cache[tax]:
        return cache[tax][name]
    # Probing by name: cached by a GET slug=neck won't work name→slug,
    # but we already did the broader pull. Try direct lookup on existing cache.
    if tax not in cache[tax]:
        cache[tax] = wp.list_tax_terms(tax)
    if name in cache[tax]:
        return cache[tax][name]
    new_id = wp.create_term(tax, name)
    if new_id:
        cache[tax][name] = new_id
    return new_id


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #

def cmd_run(args, env) -> int:
    wp    = WordPress(env["WORDPRESS_BASE_URL"], env["WORDPRESS_USERNAME"], env["WORDPRESS_APP_PASSWORD"])
    spt   = Spotify(env["SPOTIFY_CLIENT_ID"], env["SPOTIFY_CLIENT_SECRET"])
    lfm   = LastFM(env["LASTFM_API_KEY"])

    tag_id_to_name: dict[int, str] = {}
    log.info("Fetching tag dictionary (534 tags)…")
    wp.list_tags(tag_id_to_name)

    tax_term_cache: dict[str, dict[str, int]] = {}
    # Pre-warm the three custom taxonomies + the 4 release_type terms + genre seed (none).
    for tax in ("artist", "genre", "release_type"):
        tax_term_cache[tax] = wp.list_tax_terms(tax)
    for term in ("Album", "EP", "Single", "Compilation"):
        _ensure_term(wp, tax_term_cache, "release_type", term)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    planned_path = out_dir / "planned_patches.json"
    unresolved_path = out_dir / "unresolved.json"

    planned: list[dict] = []
    unresolved: list[dict] = []

    limit = args.limit if args.limit is not None else None
    offset = args.offset
    seen = 0
    total_done = 0

    for post in wp.list_posts(per_page=100):
        if seen < offset:
            seen += 1
            continue
        if limit is not None and total_done >= limit:
            break
        total_done += 1
        seen += 1

        body = enrich(post, spt, lfm, tag_id_to_name, tax_term_cache, wp)

        if body is None:
            continue
        if body.get("__unresolved__"):
            unresolved.append({
                "post_id": post["id"], "title": post["title"]["rendered"],
                "tags": post.get("tags", []), "candidates": body["candidates"],
            })
            continue

        if args.dry_run:
            planned.append({"post_id": post["id"], "title": post["title"]["rendered"],
                            "body": body})
        else:
            wp.update_post(post["id"], body)
            log.info("post %d written", post["id"])
            # 1 req/s uniformity (Spotify already covers its own quota).
            time.sleep(0.5)

    planned_path.write_text(json.dumps(planned, indent=2, ensure_ascii=False), encoding="utf-8")
    unresolved_path.write_text(json.dumps(unresolved, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Done — %d planned, %d unresolved.  planned=%s  unresolved=%s",
             len(planned), len(unresolved), planned_path, unresolved_path)
    return 0


def cmd_stats(args, env) -> int:
    wp = WordPress(env["WORDPRESS_BASE_URL"], env["WORDPRESS_USERNAME"], env["WORDPRESS_APP_PASSWORD"])
    tag_id_to_name: dict[int, str] = {}
    wp.list_tags(tag_id_to_name)

    counts = {f: 0 for f in AUTO_FILLABLE_FIELDS}
    total_posts = 0
    fully_filled_posts = 0

    tax_term_present = {"artist": 0, "genre": 0, "release_type": 0}

    for post in wp.list_posts(per_page=100):
        total_posts += 1
        acf = post.get("acf") or {}
        post_filled = True
        for f in AUTO_FILLABLE_FIELDS:
            if is_field_present(f, acf.get(f)):
                counts[f] += 1
            else:
                post_filled = False
        if post_filled:
            fully_filled_posts += 1
        for tax in tax_term_present:
            if post.get(tax):
                tax_term_present[tax] += 1

    print(f"Total posts: {total_posts}")
    print(f"Fully filled: {fully_filled_posts}")
    print("Auto-fillable field fill count:")
    for f in AUTO_FILLABLE_FIELDS:
        print(f"  {f}: {counts[f]}")
    print("Posts with at least one term in each custom taxonomy:")
    for tax, n in tax_term_present.items():
        print(f"  {tax}: {n}")
    return 0


def cmd_fuzzy(args, env) -> int:
    spt = Spotify(env["SPOTIFY_CLIENT_ID"], env["SPOTIFY_CLIENT_SECRET"])
    q_title = _norm_title(args.title)
    q_artists = [_norm_artist(a) for a in args.artists]
    print(f"q_title={q_title!r}  q_artists={q_artists!r}")
    cands = search_ladder(spt, q_title, q_artists)
    for c in cands:
        score = _score(c, q_title, q_artists)
        print(f"  score={score:.3f}  {c['id']}  {c['name']!r}  by {[a['name'] for a in c['artists']]}  ({c.get('total_tracks')} tracks, {c.get('release_date')})")
    print(f"\nTop pick: {best_candidate(cands, q_title, q_artists) or 'no winner'}")
    return 0


# --------------------------------------------------------------------------- #
# .env loader
# --------------------------------------------------------------------------- #

def load_env(path: str | None) -> dict[str, str]:
    env: dict[str, str] = {}
    if path:
        for ln in Path(path).read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            if "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("WORDPRESS_BASE_URL", "WORDPRESS_USERNAME", "WORDPRESS_APP_PASSWORD",
              "LASTFM_API_KEY", "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"):
        env.setdefault(k, os.environ.get(k, ""))
        if not env[k]:
            log.warning("env %s is missing", k)
    return env


# --------------------------------------------------------------------------- #
# CLI plumbing
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="post_to_album",
                                 description="Verbose Python CLI to backfill SCF music metadata "
                                             "from Spotify (album + tracks) and Last.fm (genre + mood).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    base = argparse.ArgumentParser(add_help=False)
    base.add_argument("--env", default=".env", help="path to .env (default: .env)")
    base.add_argument("--quiet", action="store_true")
    base.add_argument("--verbose", "-v", action="store_true")

    run = sub.add_parser("run", parents=[base], help="process posts and dry-run or apply")
    run.add_argument("--all", action="store_true", help="(default) process all posts")
    run.add_argument("--limit", type=int, help="process at most N posts")
    run.add_argument("--offset", type=int, default=0, help="skip first M posts")
    run.add_argument("--dry-run", action="store_true", help="dump planned patches to ./out/ (default)")
    run.add_argument("--apply", action="store_true", help="write to WordPress")
    run.add_argument("--out-dir", default="out", help="directory for dry-run JSON")

    stats = sub.add_parser("stats", parents=[base], help="report fill-rate before/after")

    fuzzy = sub.add_parser("fuzzy", parents=[base], help="debug-search Spotify for a (title, artists…) pair")
    fuzzy.add_argument("title")
    fuzzy.add_argument("artists", nargs="*")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    level = logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    if args.cmd == "run":
        # resolve apply/dry-run default
        if not args.dry_run and not args.apply:
            args.dry_run = True
        if args.dry_run and args.apply:
            ap.error("--dry-run and --apply are mutually exclusive")
        return cmd_run(args, load_env(args.env))
    if args.cmd == "stats":
        return cmd_stats(args, load_env(args.env))
    if args.cmd == "fuzzy":
        return cmd_fuzzy(args, load_env(args.env))
    ap.error("unknown subcommand")
    return 2


if __name__ == "__main__":
    sys.exit(main())
