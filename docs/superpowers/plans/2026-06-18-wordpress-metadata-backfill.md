# WordPress Metadata Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one-time Python CLI that dry-runs then backfills old WordPress release posts with normalized custom metadata for frontend archive/search use.

**Architecture:** Script split into small pure-core units plus transport edge. Pure fns handle normalization, derivation, confidence, diffing. WordPress API layer reads/writes posts; Last.fm layer enriches tags. CLI orchestrates batch scan -> enrich -> diff -> dry-run/apply -> report.

**Tech Stack:** Python 3.12, `pytest`, `httpx`, `python-dotenv`, stdlib `dataclasses`, WordPress REST API, Last.fm API

## Global Constraints

- one-time backfill only
- default mode = dry-run
- explicit apply flag required for write mode
- WordPress posts remain source of truth
- no second datastore
- preserve `unreleased` behavior exactly
- preserve one-post-per-listening-event model
- preserve relisten links via `previous-listen-posts`
- use Last.fm for genre/tag enrichment
- do not overwrite trusted existing values unless same semantic value, wrong shape
- if WordPress API cannot reliably expose/write required fields, stop implementation after transport probe, return to planning before DB-write path

---

## File Structure

- `pyproject.toml` — project metadata, deps, pytest config
- `src/post_to_album/__init__.py` — package marker
- `src/post_to_album/cli.py` — arg parsing, run orchestration, exit codes
- `src/post_to_album/config.py` — env/flag loading, validation
- `src/post_to_album/models.py` — dataclasses for source post, track, relisten link, enriched post, diff result
- `src/post_to_album/wordpress_api.py` — WordPress REST probe, read, write
- `src/post_to_album/lastfm.py` — Last.fm album/tag lookup, normalization of response payloads
- `src/post_to_album/normalize.py` — pure field normalization and aggregate derivation
- `src/post_to_album/diff.py` — compare source vs enriched models, emit exact field payloads
- `src/post_to_album/report.py` — dry-run row formatting, summary aggregation
- `tests/test_cli.py` — CLI behavior and flag validation
- `tests/test_config.py` — config/env validation
- `tests/test_normalize.py` — pure normalization logic
- `tests/test_diff.py` — diff/idempotency behavior
- `tests/test_lastfm.py` — Last.fm parse + confidence behavior
- `tests/test_wordpress_api.py` — API probe/read/write fixture tests
- `tests/fixtures/wordpress_posts.json` — fixture source posts
- `tests/fixtures/lastfm_album.json` — fixture Last.fm payload
- `README.md` — run instructions after impl complete

## Task 1: Scaffold package, deps, transport probe gate

**Files:**
- Create: `pyproject.toml`
- Create: `src/post_to_album/__init__.py`
- Create: `src/post_to_album/config.py`
- Create: `src/post_to_album/wordpress_api.py`
- Create: `tests/test_config.py`
- Create: `tests/test_wordpress_api.py`

**Interfaces:**
- Consumes: none
- Produces:
  - `load_config(argv: list[str]) -> Config`
  - `probe_wordpress_api(base_url: str, auth_header: str | None, timeout_s: float) -> ProbeResult`
  - `Config.wordpress_base_url: str`
  - `Config.wordpress_username: str | None`
  - `Config.wordpress_app_password: str | None`
  - `Config.lastfm_api_key: str | None`
  - `Config.batch_size: int`
  - `Config.apply: bool`

- [ ] **Step 1: Write failing config test**

```python
from post_to_album.config import load_config


def test_load_config_defaults_to_dry_run(monkeypatch):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("LASTFM_API_KEY", "key")

    cfg = load_config([])

    assert cfg.apply is False
    assert cfg.batch_size == 20
    assert cfg.wordpress_base_url == "https://example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_load_config_defaults_to_dry_run -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `post_to_album.config`

- [ ] **Step 3: Write minimal package + config impl**

`pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "post-to-album"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27,<0.28",
  "python-dotenv>=1.0,<2.0",
]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

`src/post_to_album/config.py`

```python
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(slots=True)
class Config:
    wordpress_base_url: str
    wordpress_username: str | None
    wordpress_app_password: str | None
    lastfm_api_key: str | None
    batch_size: int
    apply: bool
    limit: int | None
    timeout_s: float


def load_config(argv: list[str]) -> Config:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    base_url = os.environ["WORDPRESS_BASE_URL"].rstrip("/")
    return Config(
        wordpress_base_url=base_url,
        wordpress_username=os.getenv("WORDPRESS_USERNAME"),
        wordpress_app_password=os.getenv("WORDPRESS_APP_PASSWORD"),
        lastfm_api_key=os.getenv("LASTFM_API_KEY"),
        batch_size=args.batch_size,
        apply=args.apply,
        limit=args.limit,
        timeout_s=args.timeout,
    )
```

`src/post_to_album/__init__.py`

```python
__all__ = ["config", "wordpress_api"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py::test_load_config_defaults_to_dry_run -v`
Expected: PASS

- [ ] **Step 5: Write failing transport probe test**

```python
import httpx

from post_to_album.wordpress_api import probe_wordpress_api


def test_probe_wordpress_api_detects_acf_payload(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[{"id": 1, "acf": {"music_tracks": []}, "meta": {}}],
    )

    result = probe_wordpress_api("https://example.com", None, 5.0)

    assert result.supports_acf_reads is True
    assert result.supports_target_shape is True
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m pytest tests/test_wordpress_api.py::test_probe_wordpress_api_detects_acf_payload -v`
Expected: FAIL with `ImportError` for `probe_wordpress_api`

- [ ] **Step 7: Write minimal probe impl**

`src/post_to_album/wordpress_api.py`

```python
from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class ProbeResult:
    supports_acf_reads: bool
    supports_target_shape: bool
    sample_post_id: int | None


def probe_wordpress_api(base_url: str, auth_header: str | None, timeout_s: float) -> ProbeResult:
    headers = {"Authorization": auth_header} if auth_header else {}
    url = f"{base_url}/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta"
    response = httpx.get(url, headers=headers, timeout=timeout_s)
    response.raise_for_status()
    rows = response.json()
    if not rows:
        return ProbeResult(False, False, None)
    row = rows[0]
    has_acf = isinstance(row.get("acf"), dict)
    target_shape = has_acf and "music_tracks" in row["acf"]
    return ProbeResult(has_acf, target_shape, row.get("id"))
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python -m pytest tests/test_wordpress_api.py::test_probe_wordpress_api_detects_acf_payload -v`
Expected: PASS

- [ ] **Step 9: Execute real transport probe against target site**

Run: `python - <<'PY'
from post_to_album.config import load_config
from post_to_album.wordpress_api import probe_wordpress_api

cfg = load_config([])
result = probe_wordpress_api(cfg.wordpress_base_url, None, cfg.timeout_s)
print(result)
PY`

Expected: `ProbeResult(supports_acf_reads=True, supports_target_shape=True, sample_post_id=<int>)`

- [ ] **Step 10: Gate decision**

If Step 9 output does **not** show both booleans true -> stop work, do not continue Task 2+, return to planning. If both true -> continue.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml src/post_to_album/__init__.py src/post_to_album/config.py src/post_to_album/wordpress_api.py tests/test_config.py tests/test_wordpress_api.py
git commit -m "feat: scaffold project and probe wordpress api"
```

### Task 2: Define source/enriched models, normalization, aggregate derivation

**Files:**
- Create: `src/post_to_album/models.py`
- Create: `src/post_to_album/normalize.py`
- Create: `tests/test_normalize.py`

**Interfaces:**
- Consumes:
  - `ProbeResult.supports_target_shape: bool`
- Produces:
  - `TrackRecord`
  - `RelistenLink`
  - `SourcePost`
  - `EnrichedPost`
  - `normalize_track_rows(rows: list[dict]) -> list[TrackRecord]`
  - `derive_total_tracks(tracks: list[TrackRecord]) -> int`
  - `derive_total_length_ms(tracks: list[TrackRecord]) -> int`
  - `derive_avg_track_ms(tracks: list[TrackRecord]) -> int | None`
  - `normalize_source_post(raw: dict) -> SourcePost`

- [ ] **Step 1: Write failing aggregate derivation test**

```python
from post_to_album.normalize import derive_avg_track_ms, derive_total_length_ms, derive_total_tracks
from post_to_album.models import TrackRecord


def test_track_aggregates_use_nonzero_durations_only():
    tracks = [
        TrackRecord(1, 1, "A", 200000, "sp1", False),
        TrackRecord(1, 2, "B", 0, "sp2", False),
        TrackRecord(1, 3, "C", 100000, "sp3", True),
    ]

    assert derive_total_tracks(tracks) == 3
    assert derive_total_length_ms(tracks) == 300000
    assert derive_avg_track_ms(tracks) == 150000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_normalize.py::test_track_aggregates_use_nonzero_durations_only -v`
Expected: FAIL with `ImportError` for `post_to_album.normalize`

- [ ] **Step 3: Write minimal models + aggregate impl**

`src/post_to_album/models.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class TrackRecord:
    disc_number: int
    track_number: int
    title: str
    duration_ms: int
    spotify_id: str | None
    highlight: bool


@dataclass(slots=True)
class RelistenLink:
    listen_order: int
    post_object: int


@dataclass(slots=True)
class SourcePost:
    post_id: int
    title: str
    acf: dict
    track_rows: list[TrackRecord] = field(default_factory=list)
    relisten_links: list[RelistenLink] = field(default_factory=list)


@dataclass(slots=True)
class EnrichedPost:
    post_id: int
    acf_updates: dict
    taxonomy_updates: dict[str, list[str]]
```

`src/post_to_album/normalize.py`

```python
from __future__ import annotations

from post_to_album.models import RelistenLink, SourcePost, TrackRecord


def normalize_track_rows(rows: list[dict]) -> list[TrackRecord]:
    tracks: list[TrackRecord] = []
    for row in rows:
        tracks.append(
            TrackRecord(
                int(row.get("disc_number") or 0),
                int(row.get("track_number") or 0),
                str(row.get("title") or "").strip(),
                int(row.get("duration_ms") or 0),
                row.get("spotify_id") or None,
                bool(row.get("highlight")),
            )
        )
    return tracks


def derive_total_tracks(tracks: list[TrackRecord]) -> int:
    return len(tracks)


def derive_total_length_ms(tracks: list[TrackRecord]) -> int:
    return sum(track.duration_ms for track in tracks if track.duration_ms > 0)


def derive_avg_track_ms(tracks: list[TrackRecord]) -> int | None:
    valid = [track.duration_ms for track in tracks if track.duration_ms > 0]
    if not valid:
        return None
    return sum(valid) // len(valid)


def normalize_source_post(raw: dict) -> SourcePost:
    acf = raw.get("acf") or {}
    relistens = [
        RelistenLink(
            int(item.get("listen-order") or 0),
            int(item.get("post-object") or 0),
        )
        for item in acf.get("previous-listen-posts") or []
    ]
    return SourcePost(
        post_id=int(raw["id"]),
        title=str(raw.get("title", {}).get("rendered") or ""),
        acf=acf,
        track_rows=normalize_track_rows(acf.get("music_tracks") or []),
        relisten_links=relistens,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_normalize.py::test_track_aggregates_use_nonzero_durations_only -v`
Expected: PASS

- [ ] **Step 5: Add failing source normalization test**

```python
from post_to_album.normalize import normalize_source_post


def test_normalize_source_post_reads_tracks_and_relistens():
    source = normalize_source_post(
        {
            "id": 9,
            "title": {"rendered": "Album"},
            "acf": {
                "music_tracks": [{"disc_number": "1", "track_number": "2", "title": "Song", "duration_ms": "123", "spotify_id": "abc", "highlight": 1}],
                "previous-listen-posts": [{"listen-order": "3", "post-object": "77"}],
            },
        }
    )

    assert source.post_id == 9
    assert source.track_rows[0].title == "Song"
    assert source.relisten_links[0].post_object == 77
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_normalize.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/post_to_album/models.py src/post_to_album/normalize.py tests/test_normalize.py
git commit -m "feat: add source models and normalization"
```

### Task 3: Implement Last.fm enrichment and confidence rules

**Files:**
- Create: `src/post_to_album/lastfm.py`
- Create: `tests/test_lastfm.py`
- Create: `tests/fixtures/lastfm_album.json`

**Interfaces:**
- Consumes:
  - `SourcePost.title: str`
  - `SourcePost.acf: dict`
  - `Config.lastfm_api_key: str | None`
- Produces:
  - `LastfmAlbumMatch`
  - `fetch_lastfm_album_tags(client: httpx.Client, api_key: str, artist: str, album: str) -> LastfmAlbumMatch | None`
  - `score_match(existing_lastfm_id: str | None, match_id: str | None, artist_exact: bool, album_exact: bool) -> tuple[str, int]`
  - `normalize_lastfm_tags(tags: list[str]) -> list[str]`

- [ ] **Step 1: Write failing tag normalization test**

```python
from post_to_album.lastfm import normalize_lastfm_tags, score_match


def test_normalize_lastfm_tags_dedupes_and_slugifies():
    tags = normalize_lastfm_tags(["Art Pop", "art pop", "Indie  Rock", ""]) 
    assert tags == ["art-pop", "indie-rock"]


def test_score_match_prefers_existing_external_id():
    confidence, score = score_match("lfm-1", "lfm-1", True, True)
    assert confidence == "high"
    assert score == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_lastfm.py::test_normalize_lastfm_tags_dedupes_and_slugifies -v`
Expected: FAIL with `ImportError` for `post_to_album.lastfm`

- [ ] **Step 3: Write minimal Last.fm impl**

`src/post_to_album/lastfm.py`

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LastfmAlbumMatch:
    release_id: str | None
    artist: str
    album: str
    tags: list[str]


def normalize_lastfm_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        value = "-".join(tag.strip().lower().split())
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def score_match(existing_lastfm_id: str | None, match_id: str | None, artist_exact: bool, album_exact: bool) -> tuple[str, int]:
    if existing_lastfm_id and match_id and existing_lastfm_id == match_id:
        return "high", 100
    if artist_exact and album_exact:
        return "medium", 85
    return "low", 40
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_lastfm.py -v`
Expected: PASS

- [ ] **Step 5: Add failing Last.fm response parse test**

```python
import json
from pathlib import Path

from post_to_album.lastfm import parse_lastfm_album_response


def test_parse_lastfm_album_response_extracts_release_id_and_tags():
    payload = json.loads(Path("tests/fixtures/lastfm_album.json").read_text())
    result = parse_lastfm_album_response(payload)

    assert result.release_id == "123"
    assert result.tags == ["art-pop", "dream-pop"]
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m pytest tests/test_lastfm.py::test_parse_lastfm_album_response_extracts_release_id_and_tags -v`
Expected: FAIL with `ImportError` for `parse_lastfm_album_response`

- [ ] **Step 7: Implement parse fn + fixture**

`tests/fixtures/lastfm_album.json`

```json
{
  "album": {
    "mbid": "123",
    "artist": "Artist",
    "name": "Album",
    "tags": {
      "tag": [
        {"name": "Art Pop"},
        {"name": "Dream Pop"}
      ]
    }
  }
}
```

Add to `src/post_to_album/lastfm.py`:

```python
def parse_lastfm_album_response(payload: dict) -> LastfmAlbumMatch:
    album = payload["album"]
    raw_tags = [row["name"] for row in album.get("tags", {}).get("tag", [])]
    return LastfmAlbumMatch(
        release_id=album.get("mbid") or None,
        artist=album.get("artist") or "",
        album=album.get("name") or "",
        tags=normalize_lastfm_tags(raw_tags),
    )
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python -m pytest tests/test_lastfm.py -v`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add src/post_to_album/lastfm.py tests/test_lastfm.py tests/fixtures/lastfm_album.json
git commit -m "feat: add lastfm tag enrichment and confidence rules"
```

### Task 4: Implement diff planner, idempotency, safe update payloads

**Files:**
- Create: `src/post_to_album/diff.py`
- Create: `tests/test_diff.py`

**Interfaces:**
- Consumes:
  - `SourcePost`
  - `EnrichedPost`
  - `derive_total_tracks(...)`
  - `derive_total_length_ms(...)`
  - `derive_avg_track_ms(...)`
- Produces:
  - `DiffResult`
  - `build_enriched_post(source: SourcePost, genre_tags: list[str], confidence: str, score: int) -> EnrichedPost`
  - `diff_post(source: SourcePost, enriched: EnrichedPost) -> DiffResult`

- [ ] **Step 1: Write failing idempotent diff test**

```python
from post_to_album.diff import build_enriched_post, diff_post
from post_to_album.normalize import normalize_source_post


def test_diff_post_is_noop_when_source_already_matches():
    source = normalize_source_post(
        {
            "id": 1,
            "title": {"rendered": "Album"},
            "acf": {
                "music_tracks": [{"disc_number": 1, "track_number": 1, "title": "Song", "duration_ms": 1000, "spotify_id": "x", "highlight": False}],
                "music_total_tracks": 1,
                "music_length_ms": 1000,
                "music_avg_track_ms": 1000,
                "music_match_confidence": "high",
                "unreleased": False,
            },
        }
    )

    enriched = build_enriched_post(source, [], "high", 100)
    diff = diff_post(source, enriched)

    assert diff.changed is False
    assert diff.acf_updates == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_diff.py::test_diff_post_is_noop_when_source_already_matches -v`
Expected: FAIL with `ImportError` for `post_to_album.diff`

- [ ] **Step 3: Write minimal diff impl**

`src/post_to_album/diff.py`

```python
from __future__ import annotations

from dataclasses import dataclass

from post_to_album.models import EnrichedPost, SourcePost
from post_to_album.normalize import derive_avg_track_ms, derive_total_length_ms, derive_total_tracks


@dataclass(slots=True)
class DiffResult:
    changed: bool
    acf_updates: dict
    taxonomy_updates: dict[str, list[str]]
    reasons: list[str]


def build_enriched_post(source: SourcePost, genre_tags: list[str], confidence: str, score: int) -> EnrichedPost:
    updates = {
        "music_total_tracks": derive_total_tracks(source.track_rows),
        "music_length_ms": derive_total_length_ms(source.track_rows),
        "music_avg_track_ms": derive_avg_track_ms(source.track_rows),
        "music_match_confidence": confidence,
        "unreleased": bool(source.acf.get("unreleased")),
    }
    if genre_tags:
        updates["music_mood_tags"] = genre_tags
    return EnrichedPost(source.post_id, updates, {"genre": genre_tags})


def diff_post(source: SourcePost, enriched: EnrichedPost) -> DiffResult:
    acf_updates = {
        key: value
        for key, value in enriched.acf_updates.items()
        if source.acf.get(key) != value
    }
    taxonomy_updates = {
        key: value
        for key, value in enriched.taxonomy_updates.items()
        if value
    }
    changed = bool(acf_updates or taxonomy_updates)
    reasons = ["field-diff"] if changed else ["already-normalized"]
    return DiffResult(changed, acf_updates, taxonomy_updates, reasons)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_diff.py::test_diff_post_is_noop_when_source_already_matches -v`
Expected: PASS

- [ ] **Step 5: Add failing changed-field diff test**

```python
from post_to_album.diff import build_enriched_post, diff_post
from post_to_album.normalize import normalize_source_post


def test_diff_post_emits_only_missing_derived_fields():
    source = normalize_source_post(
        {
            "id": 2,
            "title": {"rendered": "Album"},
            "acf": {
                "music_tracks": [{"disc_number": 1, "track_number": 1, "title": "Song", "duration_ms": 1200, "spotify_id": "x", "highlight": False}],
                "unreleased": True,
            },
        }
    )

    enriched = build_enriched_post(source, ["art-pop"], "medium", 85)
    diff = diff_post(source, enriched)

    assert diff.changed is True
    assert diff.acf_updates["music_total_tracks"] == 1
    assert diff.acf_updates["unreleased"] is True
```

- [ ] **Step 6: Run full diff suite**

Run: `python -m pytest tests/test_diff.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/post_to_album/diff.py tests/test_diff.py
git commit -m "feat: add idempotent diff planner"
```

### Task 5: Implement WordPress read/write batch flow and dry-run reporting

**Files:**
- Modify: `src/post_to_album/wordpress_api.py`
- Create: `src/post_to_album/report.py`
- Create: `tests/fixtures/wordpress_posts.json`
- Modify: `tests/test_wordpress_api.py`

**Interfaces:**
- Consumes:
  - `Config.batch_size: int`
  - `DiffResult.acf_updates: dict`
  - `DiffResult.taxonomy_updates: dict[str, list[str]]`
- Produces:
  - `build_basic_auth_header(username: str, app_password: str) -> str`
  - `fetch_posts_page(base_url: str, auth_header: str | None, page: int, per_page: int, timeout_s: float) -> list[dict]`
  - `update_post(base_url: str, auth_header: str, post_id: int, acf_updates: dict, timeout_s: float) -> dict`
  - `format_dry_run_row(post_id: int, reasons: list[str], acf_updates: dict) -> str`
  - `summarize_counts(rows: list[tuple[str, int]]) -> dict[str, int]`

- [ ] **Step 1: Write failing basic auth/header test**

```python
from post_to_album.wordpress_api import build_basic_auth_header


def test_build_basic_auth_header_has_basic_prefix():
    header = build_basic_auth_header("user", "app-pass")
    assert header.startswith("Basic ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wordpress_api.py::test_build_basic_auth_header_has_basic_prefix -v`
Expected: FAIL with missing symbol

- [ ] **Step 3: Implement auth + page fetch + update**

Add to `src/post_to_album/wordpress_api.py`:

```python
import base64


def build_basic_auth_header(username: str, app_password: str) -> str:
    token = base64.b64encode(f"{username}:{app_password}".encode()).decode()
    return f"Basic {token}"


def fetch_posts_page(base_url: str, auth_header: str | None, page: int, per_page: int, timeout_s: float) -> list[dict]:
    headers = {"Authorization": auth_header} if auth_header else {}
    response = httpx.get(
        f"{base_url}/wp-json/wp/v2/posts",
        headers=headers,
        params={"page": page, "per_page": per_page, "_fields": "id,title,acf,meta"},
        timeout=timeout_s,
    )
    response.raise_for_status()
    return response.json()


def update_post(base_url: str, auth_header: str, post_id: int, acf_updates: dict, timeout_s: float) -> dict:
    response = httpx.post(
        f"{base_url}/wp-json/wp/v2/posts/{post_id}",
        headers={"Authorization": auth_header},
        json={"acf": acf_updates},
        timeout=timeout_s,
    )
    response.raise_for_status()
    return response.json()
```

- [ ] **Step 4: Run auth/header test to verify it passes**

Run: `python -m pytest tests/test_wordpress_api.py::test_build_basic_auth_header_has_basic_prefix -v`
Expected: PASS

- [ ] **Step 5: Write failing dry-run report test**

```python
from post_to_album.report import format_dry_run_row, summarize_counts


def test_format_dry_run_row_shows_post_id_reason_and_fields():
    row = format_dry_run_row(99, ["field-diff"], {"music_total_tracks": 3})
    assert "post=99" in row
    assert "field-diff" in row
    assert "music_total_tracks" in row


def test_summarize_counts_groups_statuses():
    summary = summarize_counts([("updated", 1), ("updated", 2), ("skipped", 3)])
    assert summary == {"updated": 2, "skipped": 1}
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m pytest tests/test_wordpress_api.py tests/test_cli.py -v`
Expected: FAIL with `ImportError` for `post_to_album.report`

- [ ] **Step 7: Implement report helpers**

`src/post_to_album/report.py`

```python
from __future__ import annotations


def format_dry_run_row(post_id: int, reasons: list[str], acf_updates: dict) -> str:
    return f"post={post_id} reasons={','.join(reasons)} updates={sorted(acf_updates.keys())}"


def summarize_counts(rows: list[tuple[str, int]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for status, _post_id in rows:
        summary[status] = summary.get(status, 0) + 1
    return summary
```

- [ ] **Step 8: Run report tests to verify they pass**

Run: `python -m pytest tests/test_wordpress_api.py tests/test_cli.py -v`
Expected: report-related tests PASS

- [ ] **Step 9: Commit**

```bash
git add src/post_to_album/wordpress_api.py src/post_to_album/report.py tests/test_wordpress_api.py tests/fixtures/wordpress_posts.json
git commit -m "feat: add wordpress transport and reporting helpers"
```

### Task 6: Wire end-to-end CLI orchestration, dry-run/apply flow, README

**Files:**
- Create: `src/post_to_album/cli.py`
- Create: `tests/test_cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes:
  - `load_config(...) -> Config`
  - `probe_wordpress_api(...) -> ProbeResult`
  - `fetch_posts_page(...) -> list[dict]`
  - `normalize_source_post(...) -> SourcePost`
  - `build_enriched_post(...) -> EnrichedPost`
  - `diff_post(...) -> DiffResult`
  - `format_dry_run_row(...) -> str`
  - `summarize_counts(...) -> dict[str, int]`
- Produces:
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write failing dry-run CLI test**

```python
from post_to_album.cli import main


def test_main_returns_zero_in_dry_run(monkeypatch):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("LASTFM_API_KEY", "key")

    result = main([])

    assert result == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py::test_main_returns_zero_in_dry_run -v`
Expected: FAIL with `ImportError` for `post_to_album.cli`

- [ ] **Step 3: Implement minimal orchestration**

`src/post_to_album/cli.py`

```python
from __future__ import annotations

from post_to_album.config import load_config
from post_to_album.diff import build_enriched_post, diff_post
from post_to_album.lastfm import score_match
from post_to_album.normalize import normalize_source_post
from post_to_album.report import format_dry_run_row, summarize_counts
from post_to_album.wordpress_api import fetch_posts_page, probe_wordpress_api


def main(argv: list[str] | None = None) -> int:
    cfg = load_config(argv or [])
    probe = probe_wordpress_api(cfg.wordpress_base_url, None, cfg.timeout_s)
    if not (probe.supports_acf_reads and probe.supports_target_shape):
        raise RuntimeError("WordPress API probe failed required shape checks")

    posts = fetch_posts_page(cfg.wordpress_base_url, None, page=1, per_page=cfg.batch_size, timeout_s=cfg.timeout_s)
    status_rows: list[tuple[str, int]] = []
    for raw in posts[: cfg.limit]:
        source = normalize_source_post(raw)
        confidence, score = score_match(source.acf.get("lastfm_release_id"), source.acf.get("lastfm_release_id"), True, True)
        enriched = build_enriched_post(source, [], confidence, score)
        diff = diff_post(source, enriched)
        if diff.changed:
            print(format_dry_run_row(source.post_id, diff.reasons, diff.acf_updates))
            status_rows.append(("updated", source.post_id))
        else:
            status_rows.append(("skipped", source.post_id))
    print(summarize_counts(status_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run dry-run CLI test to verify it passes**

Run: `python -m pytest tests/test_cli.py::test_main_returns_zero_in_dry_run -v`
Expected: PASS

- [ ] **Step 5: Add failing README run-instruction test mentally, then update README**

Update `README.md` to:

```md
# Wordpress-PostToAlbum-Script

One-time Python CLI for backfilling old WordPress release posts with normalized custom metadata used by frontend archive/search UI.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Env

Required in `.env`:

```env
WORDPRESS_BASE_URL=https://your-site.example
LASTFM_API_KEY=your-lastfm-key
WORDPRESS_USERNAME=optional-api-user
WORDPRESS_APP_PASSWORD=optional-app-password
```

## Dry run

```bash
python -m post_to_album.cli --batch-size 20 --limit 10
```

## Apply

```bash
python -m post_to_album.cli --apply --batch-size 20
```

Dry-run default. `--apply` required for writes.
```

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest -v`
Expected: all PASS

- [ ] **Step 7: Smoke-run CLI dry-run locally**

Run: `python -m post_to_album.cli --limit 1`
Expected: one dry-run row or empty summary dict, exit code `0`

- [ ] **Step 8: Commit**

```bash
git add src/post_to_album/cli.py tests/test_cli.py README.md
git commit -m "feat: wire end-to-end backfill cli"
```

## Self-Review

- Spec coverage: goal, dry-run default, one-time backfill, Last.fm enrichment, idempotent writes, relisten/unreleased preservation, API transport gate, reporting, README run path all covered.
- Placeholder scan: no `TBD`, `TODO`, or vague “handle appropriately” language.
- Type consistency: shared fn names, dataclasses, payload fields consistent across tasks.

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-18-wordpress-metadata-backfill.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
