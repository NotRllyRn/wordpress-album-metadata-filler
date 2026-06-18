# WordPress Metadata Backfill Design

## Goal

Backfill old WordPress release posts with normalized custom metadata so frontend UI can fetch consistent archive/search data from CMS without re-running script after migration window.

## Problem

New posts will have custom fields filled automatically. Old posts do not. Frontend needs one canonical shape across all release posts for:

- archive filtering
- archive search
- stable release metadata display
- future automation consuming normalized CMS data

Live site already uses WordPress posts as source of truth. Script must enrich existing posts in place, not create parallel datastore.

## Current Known Schema

Based on `scf-export-2026-06-18.json`, site uses:

- post type: `post`
- custom taxonomies: `artist`, `genre`, `release_type`
- ACF/SCF group: `meta`

Important fields:

- `music_tracks`
- `music_rating`
- `music_favorite`
- `music_length_ms`
- `spotify_album_id`
- `spotify_album_url`
- `music_release_date`
- `music_listened_at`
- `lastfm_release_id`
- `music_review_status`
- `music_match_confidence`
- `music_total_tracks`
- `music_avg_track_ms`
- `music_explicit`
- `music_label`
- `music_mood_tags`
- `music_notes`
- `music_source`
- `previous-listen-posts`
- `unreleased`

Important repeater shape:

- `music_tracks[].disc_number`
- `music_tracks[].track_number`
- `music_tracks[].title`
- `music_tracks[].duration_ms`
- `music_tracks[].spotify_id`
- `music_tracks[].highlight`

Relisten shape:

- `previous-listen-posts[].listen-order`
- `previous-listen-posts[].post-object`

Business rules already confirmed:

- every WordPress post relevant here = release post
- one post = one listening event
- relistens linked via `previous-listen-posts`
- `unreleased` behavior must stay intact
- Last.fm preferred for genre/tag enrichment

## Primary Use Cases

### 1. Frontend archive filtering

Frontend must filter releases by normalized genre, release type, favorite flag, review status, explicit flag, unreleased flag, artist, date fields.

### 2. Frontend archive search and listing

Frontend must read consistent album metadata across old and new posts without fallback parsing or per-post repair logic.

### 3. Canonical CMS dataset

CMS becomes stable backend contract for future frontend features and future automation.

## Non-Goals

- no ongoing sync daemon
- no cron job
- no second datastore
- no new editorial workflow for future posts
- no destructive rewrite of already-correct metadata
- no frontend implementation in this phase

## Recommended Approach

Build one-time Python CLI backfill script with dry-run support, batch processing, deterministic enrichment, idempotent writes.

Why this approach:

- Python fits current repo intent
- one-time script matches confirmed operational model
- dry-run lowers risk on live CMS
- idempotent write logic allows safe rerun if partial failure happens
- batch/reporting makes live-site review practical

## Alternatives Considered

### A. WordPress REST-backed backfill CLI

Pros:

- safest boundary against live DB corruption
- uses supported CMS surface
- easier auth/audit/logging

Cons:

- slower
- ACF/meta write semantics may be awkward depending on plugin exposure

### B. Direct DB backfill CLI

Pros:

- fastest
- full control over reads/writes

Cons:

- higher blast radius
- must know exact WordPress meta storage shape
- harder to validate against plugin expectations

### C. Export/edit/import workflow

Pros:

- low code

Cons:

- weak repeatability
- weak safety for complex repeaters/relations
- bad fit for canonicalization logic

## Recommendation Decision

Prefer supported CMS/API write path first. Fall back to direct DB write path only if API cannot reliably update required repeaters, relations, taxonomies, or custom fields.

This means design must keep read/enrich/write layers separate so transport can switch without rewriting core enrichment logic.

## Proposed Architecture

### 1. Config layer

Responsibility:

- load env/config
- choose read/write transport
- set batch size, limits, dry-run, logging behavior

Inputs:

- WordPress base URL or DB credentials
- WordPress auth credentials if API path
- Last.fm API key
- run flags

Outputs:

- validated runtime config object

### 2. Source reader

Responsibility:

- fetch candidate posts needing backfill
- load fields needed for enrichment and diffing

Inputs:

- runtime config
- pagination/batch cursor

Outputs:

- normalized in-memory post records

Selection rule:

- process posts missing required canonical fields or with incomplete field groups
- optionally allow targeting subset by post ID or limit during testing

### 3. Enrichment engine

Responsibility:

- derive canonical metadata from existing post data plus external lookups
- normalize shape before write

Examples:

- resolve Last.fm tags -> normalized genre list / mood tags
- compute `music_total_tracks`
- compute `music_length_ms`
- compute `music_avg_track_ms`
- preserve `unreleased`
- preserve existing correct review/favorite/rating data
- normalize relisten links into `previous-listen-posts`

Rules:

- fill missing values
- normalize malformed values when safe and deterministic
- do not overwrite trusted existing values unless explicit normalization rule says same semantic value, wrong shape

### 4. Diff planner

Responsibility:

- compare source record vs enriched record
- emit exact field-level changes

Outputs:

- no-op for already-good posts
- update payload for changed posts
- reason codes for skip/update/fail

Need:

- human-readable dry-run output
- machine-usable write payload

### 5. Writer

Responsibility:

- apply updates to WordPress through chosen transport
- write fields in safe order for repeaters, taxonomies, relations

Requirements:

- one post update isolated from others
- partial failure on one post must not stop whole run unless strict mode
- writer returns success/failure payload per post

### 6. Reporter

Responsibility:

- log summary at end
- emit per-post update report

Summary fields:

- scanned
- updated
- skipped
- failed
- reasons by category

## Data Flow

`config -> read posts -> enrich -> diff -> dry-run report or write -> final summary`

Per post:

`raw WordPress post -> normalized source model -> optional Last.fm lookup -> canonical metadata model -> diff -> write payload`

## Canonicalization Rules

### Must preserve

- existing correct post content/title/body/slug
- existing listen-event model
- `previous-listen-posts`
- `unreleased`
- existing trustworthy user-entered values

### Must normalize

- scalar field types
- repeater item ordering
- derived duration/count fields
- external IDs/URLs shape
- taxonomy/tag arrays for frontend use

### Must derive when possible

- genre/tag data from Last.fm
- aggregate track stats from `music_tracks`

### Must skip when confidence weak

- ambiguous external match
- conflicting existing metadata
- missing minimum source data

In weak-confidence cases:

- set/report confidence
- skip destructive write
- report manual follow-up candidate

## Matching Strategy

Use existing post metadata first. External lookup second.

Priority:

1. existing stored Spotify album ID / Last.fm release ID
2. deterministic album+artist lookup
3. reject ambiguous matches

Confidence model:

- high: exact external ID match
- medium: artist + album canonicalized exact text match
- low: fuzzy name match only

Low-confidence result -> no metadata overwrite. Report only.

## Error Handling

### Hard fail run

- invalid config
- auth failure
- external schema mismatch preventing safe writes

### Soft fail post

- one post malformed
- external lookup timeout
- ambiguous album match
- writer rejection for one payload

Behavior:

- continue batch after soft fail
- include failed post in summary
- expose exact reason

## Safety Constraints

- default mode = dry-run
- explicit apply flag required for write mode
- no bulk SQL mutation without per-post diff visibility
- no deletion of existing post meta in first version unless field recompute requires exact replacement of same field
- log before/after payload for changed posts

## Testing Strategy

### Unit tests

- field normalization
- aggregate stat derivation
- confidence scoring
- diff planner

### Fixture tests

- old post missing many fields
- post with complete good metadata
- post with malformed repeater order
- ambiguous external match
- unreleased release
- relisten chain post

### Integration tests

- API/DB reader against fixture responses
- writer payload shape for repeaters/taxonomies/relations

### Dry-run verification

- inspect sample batch output before any live write

## Open Implementation Decision

One technical decision still depends on environment inspection during planning:

- whether WordPress API surface already supports reliable update of all required custom fields/repeaters/relations

Plan must include early verification task for this. If API path fails requirements, plan switches writer transport to direct DB/meta layer while keeping same source/enrichment/diff interfaces.

## Deliverable

One Python CLI script package in this repo that can:

- inspect old posts
- dry-run proposed canonical metadata changes
- apply safe backfill once
- produce audit summary

End state:

- old posts match new post metadata contract
- frontend UI can trust CMS archive/search fields
- script retired after migration window
