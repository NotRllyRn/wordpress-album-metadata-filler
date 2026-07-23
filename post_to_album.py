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
import html
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
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

PLAN_SCHEMA_VERSION = 1
TAXONOMIES = ("artist", "genre", "release_type")
RELEASE_TYPES = frozenset(CATEGORY_MAP)
DIAGNOSTIC_CODES = frozenset({
    "spotify_missing_artist", "spotify_no_results", "spotify_low_confidence",
    "spotify_ambiguous", "spotify_provider_error", "lastfm_no_results",
    "lastfm_low_confidence", "lastfm_ambiguous", "lastfm_provider_error",
    "lastfm_identity_mismatch", "lastfm_no_mbid", "lastfm_no_tracks",
    "lastfm_track_mismatch", "lastfm_no_tags",
})

# --------------------------------------------------------------------------- #
# Tiny utilities
# --------------------------------------------------------------------------- #

def raw_query(value: str) -> str:
    """Prepare a provider query without erasing release identity."""
    return html.unescape(value or "").strip()


def match_key(value: str) -> str:
    """Minimal normalization used for comparisons only, never for writes."""
    return " ".join(unicodedata.normalize("NFC", html.unescape(value or "")).casefold().split())


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, match_key(a), match_key(b)).ratio()


# Kept as aliases for callers of the old debug helpers; they are no longer
# destructive and must not be used to prepare stored values.
def _norm_title(s: str) -> str:
    return match_key(s)


def _norm_artist(s: str) -> str:
    return match_key(s)


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
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_error(exc: BaseException) -> str:
    """Keep operational diagnostics concise and avoid response/credential dumps."""
    text = " ".join(str(exc).split())
    return (text[:197] + "...") if len(text) > 200 else text


def write_json_atomic(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)

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

SPOTIFY_MIN_TITLE = 0.80
SPOTIFY_MIN_ARTIST = 0.70
SPOTIFY_MIN_SCORE = 0.82
SPOTIFY_MAX_TIE_GAP = 0.05


def spotify_candidate_score(cand: dict, q_title: str, q_artists: list[str]) -> dict:
    title_score = similarity(q_title, cand.get("name", ""))
    candidate_artists = [a.get("name", "") for a in cand.get("artists", [])]
    # Collaborations make "primary artist only" unsafe: compare every supplied
    # artist with every credited candidate artist and retain the best evidence.
    artist_score = max(
        (similarity(wp_artist, candidate_artist)
         for wp_artist in q_artists for candidate_artist in candidate_artists),
        default=0.0,
    )
    return {"score": 0.65 * title_score + 0.35 * artist_score,
            "title_score": title_score, "artist_score": artist_score,
            "candidate": cand}


def _score(cand: dict, q_title: str, q_artists: list[str]) -> float:
    return spotify_candidate_score(cand, q_title, q_artists)["score"]


def search_ladder(spt: Any, q_title: str, q_artists: list[str]) -> list[dict]:
    """Search strongest intent first; provider failures deliberately propagate."""
    quoted = f'album:"{q_title}"' + (f' artist:"{q_artists[0]}"' if q_artists else "")
    free = " ".join([q_title] + q_artists)
    seen: "OrderedDict[str, dict]" = OrderedDict()
    for q in (quoted, free, q_title):
        if not q.strip():
            continue
        for candidate in spt.search_albums(q, limit=10):
            candidate_id = candidate.get("id")
            if candidate_id:
                seen.setdefault(candidate_id, candidate)
    return list(seen.values())


def choose_spotify_candidate(spt_results: list[dict], q_title: str,
                             q_artists: list[str]) -> dict:
    if not q_artists:
        # A good title is not identity evidence for common album names.
        return {"candidate": None, "reason": "spotify_missing_artist"}
    passing = []
    for candidate in spt_results:
        row = spotify_candidate_score(candidate, q_title, q_artists)
        if (row["title_score"] >= SPOTIFY_MIN_TITLE and
                row["artist_score"] >= SPOTIFY_MIN_ARTIST and
                row["score"] >= SPOTIFY_MIN_SCORE):
            passing.append(row)
    passing.sort(key=lambda row: row["score"], reverse=True)
    if not passing:
        return {"candidate": None,
                "reason": "spotify_no_results" if not spt_results else "spotify_low_confidence"}
    if len(passing) > 1 and passing[0]["score"] - passing[1]["score"] < SPOTIFY_MAX_TIE_GAP:
        return {"candidate": None, "reason": "spotify_ambiguous",
                "scores": passing[:2]}
    return {**passing[0], "reason": "spotify_match"}


def best_candidate(spt_results: list[dict], q_title: str, q_artists: list[str]) -> dict | None:
    """Compatibility wrapper returning only the accepted Spotify object."""
    return choose_spotify_candidate(spt_results, q_title, q_artists).get("candidate")


# --------------------------------------------------------------------------- #
# LAST.FM
# --------------------------------------------------------------------------- #

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"


class LastFM:
    def __init__(self, api_key: str):
        self._key = api_key

    def _get(self, method: str, **params) -> dict:
        params.update({"method": method, "api_key": self._key, "format": "json"})
        req = urllib.request.Request(
            f"{LASTFM_BASE}?{urllib.parse.urlencode(params)}",
            headers={"User-Agent": "wordpress-album-metadata-filler/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            try:
                data = json.loads(response.read())
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RuntimeError("Last.fm malformed JSON response") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Last.fm malformed response")
        if data.get("error") is not None:
            raise RuntimeError(f"Last.fm {data['error']}: {data.get('message', 'unknown error')}")
        return data

    def album_search(self, album: str, limit: int = 10) -> list[dict]:
        data = self._get("album.search", album=album, limit=limit)
        results = data.get("results")
        if not isinstance(results, dict) or not isinstance(results.get("albummatches"), dict):
            raise RuntimeError("Last.fm malformed album.search response")
        matches = results["albummatches"].get("album", [])
        if not matches:
            return []
        if isinstance(matches, dict):
            return [matches]
        if isinstance(matches, list) and all(isinstance(item, dict) for item in matches):
            return matches
        raise RuntimeError("Last.fm malformed album.search matches")

    def album_getinfo(self, artist: str | None = None, album: str | None = None,
                      mbid: str | None = None, autocorrect: int = 0) -> dict:
        if not mbid and not (artist and album):
            raise ValueError("album_getinfo requires mbid or artist and album")
        params = {"mbid": mbid} if mbid else {
            "artist": artist, "album": album, "autocorrect": autocorrect}
        data = self._get("album.getinfo", **params)
        info = data.get("album")
        if not isinstance(info, dict):
            raise RuntimeError("Last.fm malformed album.getinfo response")
        return info


LASTFM_MIN_TITLE = 0.85
LASTFM_MIN_ARTIST = 0.75
LASTFM_MIN_SCORE = 0.85
LASTFM_MAX_TIE_GAP = 0.03
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")


def lastfm_candidate_score(spotify_album: dict, candidate: dict) -> dict:
    title_score = similarity(spotify_album.get("name", ""), candidate.get("name", ""))
    artist_score = max((similarity(a.get("name", ""), candidate.get("artist", ""))
                        for a in spotify_album.get("artists", [])), default=0.0)
    return {"score": 0.70 * title_score + 0.30 * artist_score,
            "title_score": title_score, "artist_score": artist_score,
            "candidate": candidate}


def choose_lastfm_candidate(spotify_album: dict, candidates: list[dict]) -> dict:
    spotify_artists = spotify_album.get("artists", [])
    if not spotify_artists:
        return {"candidate": None, "reason": "lastfm_missing_artist"}
    exact = [c for c in candidates
             if match_key(c.get("name", "")) == match_key(spotify_album.get("name", ""))
             and any(match_key(c.get("artist", "")) == match_key(a.get("name", ""))
                     for a in spotify_artists)]
    if len(exact) == 1:
        return {**lastfm_candidate_score(spotify_album, exact[0]), "reason": "lastfm_exact"}
    if len(exact) > 1:
        # Only a single syntactically valid, unique MBID can safely distinguish
        # duplicate exact search rows; result order is not identity evidence.
        usable = [c for c in exact if _UUID_RE.fullmatch(str(c.get("mbid", "")))]
        mbids = [c["mbid"].casefold() for c in usable]
        unique = [c for c in usable if mbids.count(c["mbid"].casefold()) == 1]
        if len(unique) == 1:
            return {**lastfm_candidate_score(spotify_album, unique[0]),
                    "reason": "lastfm_exact_mbid"}
        return {"candidate": None, "reason": "lastfm_ambiguous_exact"}
    passing = []
    for candidate in candidates:
        row = lastfm_candidate_score(spotify_album, candidate)
        if (row["title_score"] >= LASTFM_MIN_TITLE and
                row["artist_score"] >= LASTFM_MIN_ARTIST and
                row["score"] >= LASTFM_MIN_SCORE):
            passing.append(row)
    passing.sort(key=lambda row: row["score"], reverse=True)
    if not passing:
        return {"candidate": None,
                "reason": "lastfm_no_results" if not candidates else "lastfm_low_confidence"}
    if len(passing) > 1 and passing[0]["score"] - passing[1]["score"] < LASTFM_MAX_TIE_GAP:
        return {"candidate": None, "reason": "lastfm_ambiguous", "scores": passing[:2]}
    return {**passing[0], "reason": "lastfm_fuzzy"}


def _track_list(root: Any) -> list[dict]:
    if not root:
        return []
    if not isinstance(root, dict):
        raise RuntimeError("Last.fm malformed tracks collection")
    tracks = root.get("track", [])
    if not tracks:
        return []
    if isinstance(tracks, dict):
        return [tracks]
    if isinstance(tracks, list) and all(isinstance(track, dict) for track in tracks):
        return tracks
    raise RuntimeError("Last.fm malformed track entry")


def validate_lastfm_info(spotify_album: dict, spotify_tracks: list[dict],
                         candidate: dict, info: dict) -> dict:
    returned = {"name": info.get("name", ""), "artist": info.get("artist", "")}
    spotify_score = lastfm_candidate_score(spotify_album, returned)
    candidate_title = similarity(info.get("name", ""), candidate.get("name", ""))
    candidate_artist = similarity(info.get("artist", ""), candidate.get("artist", ""))
    if (spotify_score["title_score"] < LASTFM_MIN_TITLE or
            spotify_score["artist_score"] < LASTFM_MIN_ARTIST or
            candidate_title < LASTFM_MIN_TITLE or candidate_artist < LASTFM_MIN_ARTIST):
        return {"accepted": False, "reason": "lastfm_identity_changed"}
    lastfm_keys = []
    for track in _track_list(info.get("tracks")):
        track_name = track.get("name") or track.get("title") or ""
        if not isinstance(track_name, str):
            raise RuntimeError("Last.fm malformed track name")
        key = match_key(track_name)
        if key:
            lastfm_keys.append(key)
    if not lastfm_keys:
        return {"accepted": True, "reason": "lastfm_identity_no_tracks"}
    spotify_keys = {match_key(t.get("name", "")) for t in spotify_tracks if t.get("name")}
    overlap = len(spotify_keys & set(lastfm_keys)) / max(1, min(len(spotify_keys), len(set(lastfm_keys))))
    # Tracks are optional, but once supplied a sub-.60 overlap is affirmative
    # contradictory evidence rather than merely missing confirmation.
    if overlap < 0.60:
        return {"accepted": False, "reason": "lastfm_track_contradiction", "overlap": overlap}
    return {"accepted": True, "reason": "lastfm_validated", "overlap": overlap}


def pick_top_tags(album_info: dict, max_n: int, blocklist: Iterable[str],
                  artist_names: Iterable[str] = ()) -> list[str]:
    """Handles four return shapes:
         ''                       (no tags)
         {'tag': []}              (no tags)
         {'tag': [{name:'…'}]}    (multiple tags, list of dicts)
         {'tag': {name:'…'}}      (single tag — dict, not list!)
    Last.fm may also return tags as bare strings.
    """
    tags = ((album_info or {}).get("toptags") or
            (album_info or {}).get("tags") or {})
    raw = tags.get("tag", []) if isinstance(tags, dict) else []
    if isinstance(raw, (dict, str)):
        raw = [raw]
    elif not isinstance(raw, list):
        raw = []
    pats = [re.compile(p, re.IGNORECASE) for p in blocklist]
    artist_keys = {match_key(name) for name in artist_names}
    seen: set[str] = set()
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
        key = match_key(name)
        if any(p.match(name) for p in pats) or key in artist_keys or key in seen:
            continue
        seen.add(key)
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
        """Return every term; only a later out-of-range 400 ends pagination."""
        found: dict[str, int] = {}
        page = 1
        while True:
            try:
                rows, headers = self._req_get(
                    self._url(f"/{tax}", per_page=100, page=page))
            except urllib.error.HTTPError as exc:
                # WordPress uses this specific REST error to end headerless pagination.
                if exc.code == 400 and page > 1:
                    try:
                        error = json.loads(exc.read().decode("utf-8"))
                    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
                        raise exc
                    if (isinstance(error, dict) and
                            error.get("code") == "rest_post_invalid_page_number"):
                        return found
                raise
            if not isinstance(rows, list):
                raise RuntimeError(f"WordPress {tax} response was not a list")
            for row in rows:
                found[row["name"]] = row["id"]
            raw_pages = headers.get("X-WP-TotalPages") or headers.get("x-wp-totalpages")
            if raw_pages is not None:
                try:
                    if page >= int(raw_pages):
                        return found
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("Invalid X-WP-TotalPages header") from exc
            elif len(rows) < 100:
                return found
            page += 1

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
    if field in ("music_explicit", "music_favorite"):
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
    # False is a complete explicitness result, but remains fillable as an SCF default.
    return all((f == "music_explicit" and isinstance(acf.get(f), bool)) or
               is_field_present(f, acf.get(f))
               for f in AUTO_FILLABLE_FIELDS)


def post_is_complete(post: dict) -> bool:
    acf = post.get("acf") or {}
    return (is_fully_filled(acf) and bool(post.get("artist")) and
            bool(post.get("release_type")))


def _computed_value_valid(key: str, value: Any) -> bool:
    """Provider absence is not a value, but computed false/zero can be."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value)
    if isinstance(value, (list, dict)):
        return bool(value)
    if isinstance(value, bool):
        return key == "music_explicit"
    return isinstance(value, (int, float))


def _set_if_empty(acf_in: dict, acf_out: dict, key: str, value: Any) -> None:
    if (_computed_value_valid(key, value) and
            not is_field_present(key, acf_in.get(key))):
        acf_out[key] = value


# --------------------------------------------------------------------------- #
# Per-post enrichment
# --------------------------------------------------------------------------- #

def _diagnostic_code(reason: str) -> str:
    return {"lastfm_ambiguous_exact": "lastfm_ambiguous",
            "lastfm_identity_changed": "lastfm_identity_mismatch",
            "lastfm_track_contradiction": "lastfm_track_mismatch",
            "provider_error": "lastfm_provider_error"}.get(reason, reason)


def _unresolved(post: dict, code: str, message: str) -> dict:
    return {"post_id": post["id"], "post_title": post["title"]["rendered"],
            "diagnostics": [{"code": _diagnostic_code(code), "message": message or code}]}


def enrich(post: dict, spt: Any, lfm: Any,
           tag_id_to_name: dict[int, str]) -> dict | None:
    """Build declarative provider evidence and writes; never resolve or write WP terms."""
    pid  = post["id"]
    acf_in = post.get("acf") or {}
    title = post["title"]["rendered"]
    post_date = post["date"]
    tag_names = [tag_id_to_name.get(t, "") for t in post.get("tags", []) if t in tag_id_to_name]

    if post_is_complete(post):
        log.debug("SKIP post %d '%s' (fully filled)", pid, title)
        return None

    q_title = raw_query(title)
    q_artists = [raw_query(a) for a in tag_names if raw_query(a)]
    log.debug("post %d :: title=%r artists=%r date=%s",
              pid, title, tag_names, post_date)

    if not q_artists:
        return _unresolved(post, "spotify_missing_artist", "No artist tags were available.")
    try:
        cands = search_ladder(spt, q_title, q_artists)
    except (OSError, RuntimeError, ValueError) as exc:
        return _unresolved(post, "spotify_provider_error", _safe_error(exc))
    spotify_match = choose_spotify_candidate(cands, q_title, q_artists)
    winner = spotify_match.get("candidate")
    if winner is None:
        return _unresolved(post, spotify_match["reason"],
                           "Spotify did not produce a safe unique match.")

    log.info("post %d '%s' -> Spotify %s '%s' (%d tracks)",
             pid, title, winner["id"], winner["name"], winner.get("total_tracks", 0))

    try:
        album = spt.album(winner["id"])
        tracks = spt.all_tracks(winner["id"])
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        return _unresolved(post, "spotify_provider_error", _safe_error(exc))
    try:
        lfm_candidates = lfm.album_search(album["name"], limit=10)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        return _unresolved(post, "lastfm_provider_error", _safe_error(exc))
    lastfm_match = choose_lastfm_candidate(album, lfm_candidates)
    selected = lastfm_match.get("candidate")
    if selected is None:
        return _unresolved(post, lastfm_match["reason"],
                           "Last.fm did not produce a safe unique match.")
    try:
        mbid = selected.get("mbid")
        info = (lfm.album_getinfo(mbid=mbid) if mbid else
                lfm.album_getinfo(artist=selected.get("artist"),
                                  album=selected.get("name"), autocorrect=0))
        validation = validate_lastfm_info(album, tracks, selected, info)
    except (OSError, RuntimeError, ValueError) as exc:
        return _unresolved(post, "lastfm_provider_error", _safe_error(exc))
    if not validation["accepted"]:
        code = _diagnostic_code(validation["reason"])
        return _unresolved(post, code, "Last.fm album validation failed.")
    genre_names = pick_top_tags(
        info, max_n=3, blocklist=LFM_BLOCKLIST,
        artist_names=(a.get("name", "") for a in album.get("artists", [])),
    )
    if not genre_names:
        log.warning("post %d — no useful Last.fm genre tags for %s; leaving genre unchanged",
                    pid, album["name"])

    # Rebuilding provider-owned rows must not reset the editor-owned highlight.
    highlights = {row.get("spotify_id"): bool(row.get("highlight"))
                  for row in acf_in.get("music_tracks", [])
                  if isinstance(row, dict) and row.get("spotify_id")}
    track_rows = [
        {"disc_number":  t.get("disc_number", 1),
         "track_number": t.get("track_number", 0),
         "title":        t["name"],
         "duration_ms":  t["duration_ms"],
         "spotify_id":   t["id"],
         "highlight":    highlights.get(t["id"], False),
         "explicit":     bool(t.get("explicit", False))}
        for t in tracks
    ]
    length_ms = sum(t["duration_ms"] for t in track_rows)
    total     = album.get("total_tracks") or len(track_rows)
    rt_term_name = compute_release_type(track_rows, album.get("album_type", ""))
    rt_term_slug = rt_term_name.lower()

    acf_out: dict[str, Any] = {}
    _set_if_empty(acf_in, acf_out, "spotify_title", album.get("name"))
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
    _set_if_empty(acf_in, acf_out, "listen_count", 1)

    write: dict[str, Any] = {}
    if acf_out:
        write["acf"] = acf_out
    cat_id = CATEGORY_MAP[rt_term_name]
    categories = list(dict.fromkeys(
        [cid for cid in post.get("categories", []) if cid not in CATEGORY_MAP.values()] + [cat_id]))
    if categories != post.get("categories", []):
        write["categories"] = categories
    taxonomies = {"release_type": [rt_term_name]}
    if not post.get("artist") and tag_names:
        taxonomies["artist"] = list(dict.fromkeys(tag_names))
    if not post.get("genre") and genre_names:
        taxonomies["genre"] = genre_names
    write["taxonomies"] = taxonomies
    diagnostics = []
    if not info.get("mbid"):
        diagnostics.append({"code": "lastfm_no_mbid", "message": "Validated Last.fm album has no MBID."})
    if "overlap" not in validation:
        diagnostics.append({"code": "lastfm_no_tracks", "message": "Last.fm supplied no tracks for comparison."})
    if not genre_names:
        diagnostics.append({"code": "lastfm_no_tags", "message": "No acceptable Last.fm genre tags were returned."})
    spotify_score = spotify_match.get("score", spotify_candidate_score(winner, q_title, q_artists)["score"])
    lastfm_score = lastfm_match.get("score", lastfm_candidate_score(album, selected)["score"])
    lastfm_evidence = {"title": selected["name"], "artist": selected["artist"],
                       "score": lastfm_score}
    if info.get("mbid"): lastfm_evidence["mbid"] = info["mbid"]
    if "overlap" in validation: lastfm_evidence["track_overlap"] = validation["overlap"]
    patch = {"post_id": pid, "post_title": title,
             "matches": {"spotify": {"id": album["id"], "title": album["name"],
                                        "artists": [a["name"] for a in album.get("artists", [])],
                                        "score": spotify_score},
                         "lastfm": lastfm_evidence},
             "write": write, "diagnostics": diagnostics}
    if "modified" in post: patch["source_modified"] = post["modified"]
    return patch


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
# Plan validation and replay
# --------------------------------------------------------------------------- #

APPROVED_ACF_TYPES = {
    "spotify_title": str, "music_tracks": list, "music_length_ms": int,
    "spotify_album_id": str, "spotify_album_url": str, "music_release_date": str,
    "music_listened_at": str, "lastfm_release_id": str, "music_total_tracks": int,
    "music_avg_track_ms": int, "music_explicit": bool, "listen_count": int,
}
TRACK_KEYS = {"disc_number", "track_number", "title", "duration_ms", "spotify_id", "highlight", "explicit"}


def _plan_error(path: str, message: str) -> None:
    raise ValueError(f"Invalid plan at {path}: {message}")


def _exact(obj: Any, required: set[str], optional: set[str], path: str) -> None:
    if not isinstance(obj, dict) or set(obj) - required - optional or required - set(obj):
        _plan_error(path, "unsupported or missing keys")


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _score_value(value: Any) -> bool:
    return (type(value) in (int, float) and value == value and
            value not in (float("inf"), float("-inf")) and 0 <= value <= 1)


def validate_plan(plan: Any) -> dict:
    """Recursively validate the complete artifact before slicing or writes."""
    _exact(plan, {"schema_version", "generated_at", "patches"}, set(), "root")
    if type(plan["schema_version"]) is not int or plan["schema_version"] != PLAN_SCHEMA_VERSION:
        _plan_error("schema_version", "unsupported version")
    if not isinstance(plan["generated_at"], str) or not plan["generated_at"]:
        _plan_error("generated_at", "must be nonempty")
    if not isinstance(plan["patches"], list): _plan_error("patches", "must be list")
    seen: set[int] = set()
    for index, patch in enumerate(plan["patches"]):
        path = f"patches[{index}]"
        _exact(patch, {"post_id", "post_title", "matches", "write", "diagnostics"}, {"source_modified"}, path)
        if not _positive_int(patch["post_id"]) or patch["post_id"] in seen:
            _plan_error(path + ".post_id", "must be a unique positive integer")
        seen.add(patch["post_id"])
        if not isinstance(patch["post_title"], str): _plan_error(path + ".post_title", "must be string")
        if "source_modified" in patch and patch["source_modified"] is not None and not isinstance(patch["source_modified"], str): _plan_error(path + ".source_modified", "must be string or null")
        _exact(patch["matches"], {"spotify", "lastfm"}, set(), path + ".matches")
        spotify = patch["matches"]["spotify"]
        _exact(spotify, {"id", "title", "artists", "score"}, set(), path + ".matches.spotify")
        if (not all(isinstance(spotify[k], str) and spotify[k] for k in ("id", "title")) or
                not isinstance(spotify["artists"], list) or not spotify["artists"] or
                not all(isinstance(x, str) and x for x in spotify["artists"]) or
                not _score_value(spotify["score"])): _plan_error(path + ".matches.spotify", "invalid evidence")
        lastfm = patch["matches"]["lastfm"]
        _exact(lastfm, {"title", "artist", "score"}, {"mbid", "track_overlap"}, path + ".matches.lastfm")
        if (not all(isinstance(lastfm[k], str) and lastfm[k] for k in ("title", "artist")) or
                not _score_value(lastfm["score"])): _plan_error(path + ".matches.lastfm", "invalid evidence")
        if "track_overlap" in lastfm and not _score_value(lastfm["track_overlap"]): _plan_error(path + ".matches.lastfm.track_overlap", "invalid score")
        if "mbid" in lastfm and (not isinstance(lastfm["mbid"], str) or not lastfm["mbid"]): _plan_error(path + ".matches.lastfm.mbid", "must be nonempty")
        write = patch["write"]
        _exact(write, set(), {"acf", "categories", "taxonomies"}, path + ".write")
        if not write: _plan_error(path + ".write", "must be nonempty")
        if "acf" in write:
            if not isinstance(write["acf"], dict) or not write["acf"]: _plan_error(path + ".write.acf", "must be nonempty object")
            for key, value in write["acf"].items():
                if key not in APPROVED_ACF_TYPES or type(value) is not APPROVED_ACF_TYPES[key]: _plan_error(path + ".write.acf." + key, "unknown or wrong type")
                if isinstance(value, str) and not value: _plan_error(path + ".write.acf." + key, "must be nonempty")
            if "music_tracks" in write["acf"] and not write["acf"]["music_tracks"]:
                # Repeater replacement with an empty list would clear existing tracks.
                _plan_error(path + ".write.acf.music_tracks", "must be nonempty")
            for row in write["acf"].get("music_tracks", []):
                _exact(row, TRACK_KEYS, set(), path + ".write.acf.music_tracks[]")
                if (not all(_positive_int(row[k]) for k in ("disc_number", "track_number", "duration_ms")) or
                    type(row["highlight"]) is not bool or type(row["explicit"]) is not bool or
                    not all(isinstance(row[k], str) and row[k] for k in ("title", "spotify_id"))): _plan_error(path + ".write.acf.music_tracks[]", "invalid row")
        if "categories" in write:
            values = write["categories"]
            if not isinstance(values, list) or not values or not all(_positive_int(x) for x in values) or len(values) != len(set(values)): _plan_error(path + ".write.categories", "invalid replacement")
        if "taxonomies" in write:
            taxes = write["taxonomies"]
            if not isinstance(taxes, dict) or not taxes or set(taxes) - set(TAXONOMIES): _plan_error(path + ".write.taxonomies", "invalid object")
            for tax, names in taxes.items():
                if not isinstance(names, list) or not names or not all(isinstance(n, str) and n.strip() for n in names) or len(names) != len({match_key(n) for n in names}): _plan_error(path + ".write.taxonomies." + tax, "invalid names")
            if "release_type" in taxes and (len(taxes["release_type"]) != 1 or taxes["release_type"][0] not in RELEASE_TYPES): _plan_error(path + ".write.taxonomies.release_type", "invalid value")
        if not isinstance(patch["diagnostics"], list): _plan_error(path + ".diagnostics", "must be list")
        for diagnostic in patch["diagnostics"]:
            _exact(diagnostic, {"code", "message"}, set(), path + ".diagnostics[]")
            if diagnostic["code"] not in DIAGNOSTIC_CODES or not isinstance(diagnostic["message"], str) or not diagnostic["message"]: _plan_error(path + ".diagnostics[]", "invalid diagnostic")
    return plan


def validate_unresolved(value: Any) -> dict:
    _exact(value, {"schema_version", "unresolved"}, set(), "root")
    if type(value["schema_version"]) is not int or value["schema_version"] != PLAN_SCHEMA_VERSION:
        _plan_error("schema_version", "unsupported version")
    if not isinstance(value["unresolved"], list):
        _plan_error("unresolved", "must be list")
    seen = set()
    for index, row in enumerate(value["unresolved"]):
        path = f"unresolved[{index}]"
        _exact(row, {"post_id", "post_title", "diagnostics"}, set(), path)
        if not _positive_int(row["post_id"]) or row["post_id"] in seen:
            _plan_error(path + ".post_id", "must be a unique positive integer")
        seen.add(row["post_id"])
        if not isinstance(row["post_title"], str):
            _plan_error(path + ".post_title", "must be string")
        if not isinstance(row["diagnostics"], list) or not row["diagnostics"]:
            _plan_error(path + ".diagnostics", "must be nonempty list")
        for diagnostic in row["diagnostics"]:
            _exact(diagnostic, {"code", "message"}, set(), path + ".diagnostics[]")
            if (diagnostic["code"] not in DIAGNOSTIC_CODES or
                    not isinstance(diagnostic["message"], str) or not diagnostic["message"]):
                _plan_error(path + ".diagnostics[]", "invalid diagnostic")
    return value


def slice_items(items: list, offset: int, limit: int | None) -> list:
    if offset < 0 or (limit is not None and limit < 0):
        raise ValueError("offset and limit must be non-negative")
    return items[offset:] if limit is None else items[offset:offset + limit]


def materialize_body(write: dict, term_ids: dict[str, dict[str, int]]) -> dict:
    """Absent replacement keys remain absent; only present names become IDs."""
    body = {}
    if "acf" in write: body["acf"] = dict(write["acf"])
    if "categories" in write: body["categories"] = list(write["categories"])
    for tax, names in write.get("taxonomies", {}).items():
        body[tax] = [term_ids[tax][match_key(name)] for name in names]
    return body


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #

def _resolve_terms(wp: Any, patches: list[dict]) -> dict[str, dict[str, int]]:
    wanted = {tax: {} for tax in TAXONOMIES}
    for patch in patches:
        for tax, names in patch["write"].get("taxonomies", {}).items():
            for name in names:
                wanted[tax].setdefault(match_key(name), name)
    resolved: dict[str, dict[str, int]] = {tax: {} for tax in TAXONOMIES}
    for tax in TAXONOMIES:
        existing = wp.list_tax_terms(tax)
        resolved[tax] = {match_key(name): term_id for name, term_id in existing.items()}
        for key, name in wanted[tax].items():
            if key not in resolved[tax]:
                term_id = wp.create_term(tax, name)
                if not term_id:
                    raise RuntimeError(f"Could not resolve taxonomy term {tax}/{name}")
                resolved[tax][key] = term_id
    return resolved


def apply_patches(wp: Any, patches: list[dict]) -> tuple[list[int], list[dict]]:
    term_ids = _resolve_terms(wp, patches)  # all resolution precedes the first post update
    succeeded, failed = [], []
    for patch in patches:
        try:
            wp.update_post(patch["post_id"], materialize_body(patch["write"], term_ids))
            succeeded.append(patch["post_id"])
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            failed.append({"post_id": patch["post_id"], "message": _safe_error(exc)})
    return succeeded, failed


def cmd_run(args, env) -> int:
    wp = WordPress(env["WORDPRESS_BASE_URL"], env["WORDPRESS_USERNAME"], env["WORDPRESS_APP_PASSWORD"])
    spt = Spotify(env["SPOTIFY_CLIENT_ID"], env["SPOTIFY_CLIENT_SECRET"])
    lfm = LastFM(env["LASTFM_API_KEY"])
    tag_id_to_name: dict[int, str] = {}
    wp.list_tags(tag_id_to_name)
    planned, unresolved = [], []
    posts = slice_items(list(wp.list_posts(per_page=100)), args.offset, args.limit)
    for post in posts:
        result = enrich(post, spt, lfm, tag_id_to_name)
        if result is None:
            continue
        if "write" in result:
            planned.append(result)
        else:
            unresolved.append(result)
    plan = {"schema_version": PLAN_SCHEMA_VERSION, "generated_at": _now_iso(), "patches": planned}
    validate_plan(plan)
    out_dir = Path(args.out_dir)
    write_json_atomic(out_dir / "planned.json", plan)
    unresolved_file = validate_unresolved(
        {"schema_version": PLAN_SCHEMA_VERSION, "unresolved": unresolved})
    write_json_atomic(out_dir / "unresolved.json", unresolved_file)
    if args.apply:
        log.warning("run --apply is deprecated; use apply-plan")
        succeeded, failed = apply_patches(wp, planned)
        write_json_atomic(out_dir / "applied.json", {
            "schema_version": PLAN_SCHEMA_VERSION, "plan": str(out_dir / "planned.json"),
            "applied_at": _now_iso(), "succeeded": succeeded, "failed": failed})
        return 1 if failed else 0
    return 0


def cmd_apply_plan(args, env) -> int:
    plan = validate_plan(json.loads(Path(args.plan).read_text(encoding="utf-8")))
    selected = slice_items(plan["patches"], args.offset, args.limit)
    wp = WordPress(env["WORDPRESS_BASE_URL"], env["WORDPRESS_USERNAME"], env["WORDPRESS_APP_PASSWORD"])
    succeeded, failed = apply_patches(wp, selected)
    out_dir = Path(args.out_dir) if args.out_dir else Path(args.plan).parent
    write_json_atomic(out_dir / "applied.json", {
        "schema_version": PLAN_SCHEMA_VERSION, "plan": str(args.plan), "applied_at": _now_iso(),
        "succeeded": succeeded, "failed": failed})
    return 1 if failed else 0


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
    q_title = raw_query(args.title)
    q_artists = [raw_query(a) for a in args.artists if raw_query(a)]
    print(f"q_title={q_title!r}  q_artists={q_artists!r}")
    cands = search_ladder(spt, q_title, q_artists)
    for candidate in cands:
        row = spotify_candidate_score(candidate, q_title, q_artists)
        print(f"  score={row['score']:.3f} title={row['title_score']:.3f} "
              f"artist={row['artist_score']:.3f}  {candidate['id']}  "
              f"{candidate['name']!r}  by {[a['name'] for a in candidate.get('artists', [])]}")
    result = choose_spotify_candidate(cands, q_title, q_artists)
    print(f"\nResult: {result['reason']}; top pick: {result.get('candidate') or 'no winner'}")
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
    return env


def require_env(env: dict[str, str], *names: str) -> dict[str, str]:
    missing = [name for name in names if not env.get(name)]
    if missing:
        raise SystemExit("Missing environment variables: " + ", ".join(missing))
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

    apply_plan = sub.add_parser("apply-plan", parents=[base], help="validate and apply a saved plan")
    apply_plan.add_argument("plan")
    apply_plan.add_argument("--offset", type=int, default=0)
    apply_plan.add_argument("--limit", type=int)
    apply_plan.add_argument("--out-dir")

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

    env = load_env(args.env)
    wp_names = ("WORDPRESS_BASE_URL", "WORDPRESS_USERNAME", "WORDPRESS_APP_PASSWORD")
    if args.cmd == "run":
        if not args.dry_run and not args.apply:
            args.dry_run = True
        if args.dry_run and args.apply:
            ap.error("--dry-run and --apply are mutually exclusive")
        return cmd_run(args, require_env(env, *wp_names, "SPOTIFY_CLIENT_ID",
                                         "SPOTIFY_CLIENT_SECRET", "LASTFM_API_KEY"))
    if args.cmd == "apply-plan":
        return cmd_apply_plan(args, require_env(env, *wp_names))
    if args.cmd == "stats":
        return cmd_stats(args, require_env(env, *wp_names))
    if args.cmd == "fuzzy":
        return cmd_fuzzy(args, require_env(env, "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"))
    ap.error("unknown subcommand")
    return 2


if __name__ == "__main__":
    sys.exit(main())
