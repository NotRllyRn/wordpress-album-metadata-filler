# Plan 04: Final Integration, Documentation, and Rollout

## Goal

Integrate the completed Plan 01–03 features, verify their contracts together, reconcile documentation, and gate live rollout. This plan does not reimplement or duplicate feature work.

Plan 01 owns search/matching tests. Plan 02 owns SCF, taxonomy, and category payload tests. Plan 03 owns plan validation, serialization, pagination, and replay tests. Failures discovered here return to the owning plan rather than growing a second implementation path.

## Preconditions

- Python 3.10 or newer.
- Plans 01–03 are implemented and their feature tests pass.
- Work remains single-writer and commits remain sequential.
- Use targeted staging only. Never touch or stage `out/planned_patches.json`.
- Automated tests use fixtures/fakes and make no live WordPress writes.
- The committed July 23 export has been audited as compatible with Plan 02 and is covered by a schema contract test.

## Integration sequence

1. Run the Plan 01–03 feature suites together.
2. Run a no-write end-to-end planning test from fixture post through serialized `planned.json`.
3. Load that artifact through `apply-plan` with a fake WordPress client and verify the exact materialized REST bodies.
4. Verify command-specific credential checks and that `fuzzy` requires Spotify credentials.
5. Reconcile current Markdown documentation with the numbered plan sequence. Do not edit runtime-adjacent documentation outside the task scope until implementation work explicitly owns it.
6. Generate and manually review a one-post real plan.
7. Apply that one post in a controlled manual rollout, inspect WordPress, then proceed to a small batch and finally the library.

## Cross-feature integration tests

### Canonical identity

- Deluxe, remastered, accented, and suffixed titles survive queries and `spotify_title` writes unchanged.
- A title-only Spotify or Last.fm candidate with no artist evidence is unresolved.
- Spotify candidates below the existing gates cannot create ambiguity; two qualifying candidates with gaps of `0.049` and `0.050` verify rejection below, and acceptance at, the initial `0.05` boundary.
- Multiple exact Last.fm candidates are unresolved unless exactly one has a unique usable MBID.
- `album_getinfo` returns the inner album object and validation consumes that shape.
- Spotify and Last.fm provider failures use their supported `*_provider_error` codes, while successful empty responses use `*_no_results`; unresolved-file validation accepts each and rejects unknown or malformed diagnostics.

### Fill-only payload

- Existing nonempty ACF values cause their keys to be absent from `write.acf`.
- Missing optional values such as an MBID are omitted, not written as empty strings.
- Existing nonempty `artist` and `genre` taxonomies are omitted.
- Filling an empty `artist` or `genre` includes the complete desired name list.
- `release_type` contains exactly one computed term.
- Categories retain unrelated and marker IDs, including `93` and `200`, remove only legacy release-type IDs, and add exactly one computed release-type ID.

### Plan and replay

- Planning creates no taxonomy term and updates no post.
- The plan contains names, not taxonomy IDs, and contains only keys intended for writing.
- Unknown keys, malformed nested structures, invalid values, duplicate post IDs, and invalid taxonomy names fail before any write.
- Full-file validation occurs before `offset`/`limit` slicing; an invalid out-of-slice patch blocks all writes.
- Pagination loads every WordPress term page using response headers or end-of-page detection; it does not assume a single page or a fixed term count.
- `apply-plan` contacts neither Spotify nor Last.fm and does not refetch source posts or source tags.
- Fake WordPress assertions prove term creation is deduplicated and each post body contains integer IDs.

### Safety regression

- Automated test doubles fail immediately if a live WordPress write path is invoked.
- Dry-run and fixture tests do not modify either output artifact in the repository.
- `out/planned_patches.json` remains byte-for-byte untouched.

## Documentation ownership

At final integration, documentation must consistently state:

- The root `plan.md` is superseded by Plans 00–04.
- Plan 02's field list is the approved implementation contract, verified against the committed July 23 export.
- The committed export is deployed schema evidence and remains covered by a contract test.
- `out/planned.json` is the replay artifact; `out/planned_patches.json` is protected historical output.
- Python 3.10+, sequential operation, single writer, targeted staging, and no live writes from automated tests are required.
- Comments are reserved for non-obvious behavior and safety invariants.

Runtime behavior documentation (`README.md`, examples, schema meaning notes) is updated only in the implementation change that owns those files; Plan 00 itself changes plans only.

## Controlled rollout

1. Generate one plan without writes and manually inspect identity evidence, omitted keys, complete taxonomy fills, and preserved categories.
2. Apply that one plan and verify SCF fields, all taxonomies, marker categories, unrelated categories, and the effective release-type category ID in WordPress.
3. Verify SCF's `default_to_current_date` behavior does not replace explicitly supplied release/listened dates.
4. Exercise a deluxe/remastered release, collaboration, and no-tag Last.fm result.
5. Apply a reviewed small batch.
6. Back up WordPress using existing procedures before the complete library run.

Do not add programmatic rollback. Retain the reviewed plan and application result as operational evidence.

## Definition of done

- Plans 01–03 feature suites and the integration tests above pass.
- No automated test makes a live WordPress write.
- Matching ambiguity and provider error semantics are preserved end to end.
- Fill-only ACF/taxonomy behavior and category preservation are visible in the saved plan and replay body.
- Full plan validation precedes slicing and all writes.
- Documentation has one non-contradictory numbered source of truth.
- The real deployed export comparison is recorded before live rollout.
