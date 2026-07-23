# Plan 00: Overhaul Contract and Plan Index

## Status and purpose

This is a documentation/contract-only foundation. **Plan 00 contains no feature implementation.** The numbered sequence (`00` through `04`) is the implementation source of truth and supersedes the root `plan.md`.

The program remains a single-file Python CLI. Keep changes small, standard-library-first, and free of speculative abstractions. Add comments only when behavior or a safety invariant would otherwise be non-obvious; do not narrate straightforward code.

## Authoritative schema contract

The referenced July 23 SCF export is not present in this repository. Do not infer or fabricate `scf-export-2026-07-23.json` (or any other schema JSON). For implementation and tests, the active fields and taxonomies listed in Plan 02 are the **approved implementation contract**.

Before any live rollout, obtain the real deployed export and compare it with Plan 02: field names, field types, repeater children, REST exposure, taxonomy REST bases, and date formats must agree. A discrepancy blocks live application and requires the documentation/implementation contract to be reconciled first. The existing July 5 export is historical evidence, not authority for this overhaul.

## Cross-plan invariants

These rules apply to Plans 01–04 and override any less-specific example.

### Fill-only writes

- SCF keys with existing nonempty values are omitted from `write.acf`; they are never represented by `""`, `null`, `[]`, `0`, or `false` merely as defaults. Legitimate computed zero/false values may be written only when the field is empty and that value is semantically correct.
- For each fill-only taxonomy (`artist` and `genre`), omit its key when the post already has any assigned terms.
- When an empty taxonomy is filled, its array is the **complete desired name list**, because WordPress taxonomy arrays replace assignments rather than append to them.
- An empty taxonomy array is never emitted; taxonomy clearing is outside this overhaul.
- `release_type` is the exception to simple omission: the resulting post must have exactly one computed release-type term (`Album`, `EP`, `Single`, or `Compilation`).

### Category preservation

Category updates are replacement arrays, so every planned category list must be complete:

1. Start with all category IDs currently assigned to the post.
2. Remove only the legacy release-type IDs `5` (Single), `6` (Album), `7` (EP), and `98` (Compilation).
3. Preserve every unrelated or marker category, explicitly including `93` (Relisten) and `200` (Unreleased).
4. Add exactly one category ID corresponding to the computed release type.

Never replace the whole category list with only the computed release-type category.

### Matching and provider failures

- Raw provider strings are used for queries and stored values; comparison normalization never changes writes.
- Spotify and Last.fm matches with no artist evidence must not auto-accept on title alone. They remain unresolved unless another approved identity signal provides safe disambiguation.
- A Spotify winner is ambiguous when another candidate passes the same identity gates and trails its combined score by less than the initial `0.05` winner gap; do not add a separate tie-breaker.
- Provider/network/auth/rate-limit/malformed-response errors are distinct from successful calls returning no results. Preserve that distinction in logs, diagnostics, and unresolved records; do not silently convert provider errors into empty search results.
- Multiple exact Last.fm candidates are ambiguous. They may be accepted only when exactly one candidate has a unique, nonempty, usable MBID; otherwise do not choose by result order or fetch arbitrary candidates.
- `LastFM.album_getinfo(...)` returns the inner `album` object, not the response wrapper. All downstream examples and validators use that contract.

### Planning, replay, and safety

- Planning performs no WordPress writes, including taxonomy creation. Automated tests must never make live WordPress writes.
- `out/planned.json` is the new plan artifact. The existing `out/planned_patches.json` is historical and must never be touched, rewritten, deleted, migrated, or staged.
- Validate the entire plan before any term creation or post update. Apply `offset`/`limit` only **after full-file validation**, so malformed entries outside the requested slice still block every write. Resolve terms only for the validated slice.
- Python 3.10 or newer is required.
- Implementation uses a single writer and sequential commits. Stage only named, intended files; never use broad staging commands.

## Numbered plan ownership

| Plan | Ownership |
| --- | --- |
| [01](01-search-and-matching-plan.md) | Raw identity, Spotify/Last.fm search, candidate ambiguity, provider errors, and match validation; owns its feature tests. |
| [02](02-scf-and-wordpress-payload-plan.md) | Approved SCF field contract, fill-only payload construction, taxonomy/category preservation, and payload tests. |
| [03](03-planned-json-and-replay-plan.md) | Versioned plan artifact, full validation, offline replay, pagination, command credentials, and replay tests. |
| [04](04-implementation-order-and-tests.md) | Final integration, cross-feature tests, documentation reconciliation, and rollout gate only. |

Implement Plans 01–03 in order. Plan 04 does not re-own their unit/feature work.

## Final source-of-truth rules

| Data | Source of truth |
| --- | --- |
| Canonical album title | Raw `Spotify album["name"]`, stored in `spotify_title` |
| WordPress post title | Existing title; never rewritten |
| Artist relationships | Existing WordPress post-tag names, copied verbatim only when `artist` is empty |
| Genre classifications | Filtered tags from the validated Last.fm album, only when `genre` is empty |
| Release type | Exactly one computed `release_type` term plus its legacy category mirror |
| Existing markers/categories | Current WordPress category assignments, preserved except legacy release-type replacement |
| Spotify identity | `spotify_album_id` and `spotify_album_url` |
| Last.fm cross-reference | MusicBrainz ID returned through Last.fm, when present, in `lastfm_release_id` |
| Planned writes | `out/planned.json` |

## YAGNI boundaries

Do not add a framework, database, third-party matching library, async calls, provider plug-in system, UI, generalized migration engine, JSON Schema dependency, automatic rollback, or extra provider fallback. Keep provider responses out of the plan except for compact review evidence.

## Rollout gate

Live application is prohibited until all of the following hold:

1. The real deployed SCF export has been checked against Plan 02.
2. Plans 01–03 feature tests and Plan 04 integration tests pass without live WordPress writes.
3. A one-post plan has been manually reviewed and applied in a controlled rollout step.
4. Category markers and unrelated categories, taxonomy fill-only behavior, exact release identity, and SCF values have been verified in WordPress.
