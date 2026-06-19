# Graph Report - Wordpress-PostToAlbum-Script  (2026-06-19)

## Corpus Check
- 22 files · ~10,926 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 240 nodes · 465 edges · 12 communities (11 shown, 1 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 47 edges (avg confidence: 0.54)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `fb442147`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Normalization & Derivation|Normalization & Derivation]]
- [[_COMMUNITY_WordPress API & Auth|WordPress API & Auth]]
- [[_COMMUNITY_Last.fm Enrichment|Last.fm Enrichment]]
- [[_COMMUNITY_Diff & Post Models|Diff & Post Models]]
- [[_COMMUNITY_CLI Orchestration|CLI Orchestration]]
- [[_COMMUNITY_Spec & Plan|Spec & Plan]]
- [[_COMMUNITY_Config Loading|Config Loading]]
- [[_COMMUNITY_Reporting|Reporting]]
- [[_COMMUNITY_Package Marker|Package Marker]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]

## God Nodes (most connected - your core abstractions)
1. `main()` - 32 edges
2. `normalize_source_post()` - 21 edges
3. `build_enriched_post()` - 19 edges
4. `WordPress Metadata Backfill Design` - 18 edges
5. `SourcePost` - 15 edges
6. `WordPress Metadata Backfill — Derived Fields Plan (rewrite)` - 13 edges
7. `Track` - 12 edges
8. `_raw_post()` - 12 edges
9. `_probe_response()` - 11 edges
10. `diff_post()` - 9 edges

## Surprising Connections (you probably didn't know these)
- `diff_post()` --rationale_for--> `Idempotent Write Discipline`  [EXTRACTED]
  src/post_to_album/diff.py → docs/superpowers/specs/2026-06-18-wordpress-metadata-backfill-design.md
- `probe_wordpress_api()` --rationale_for--> `Transport Probe Gate`  [EXTRACTED]
  src/post_to_album/wordpress_api.py → docs/superpowers/specs/2026-06-18-wordpress-metadata-backfill-design.md
- `main()` --rationale_for--> `Dry-run By Default`  [EXTRACTED]
  src/post_to_album/cli.py → docs/superpowers/specs/2026-06-18-wordpress-metadata-backfill-design.md
- `score_match()` --rationale_for--> `Confidence-gated Enrichment`  [EXTRACTED]
  src/post_to_album/lastfm.py → docs/superpowers/specs/2026-06-18-wordpress-metadata-backfill-design.md
- `parse_previous_listens()` --rationale_for--> `Preserve Relisten and Unreleased`  [EXTRACTED]
  src/post_to_album/normalize.py → docs/superpowers/specs/2026-06-18-wordpress-metadata-backfill-design.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **End-to-end One-Time Backfill Pipeline** — post_to_album_cli_main, post_to_album_wordpress_api_probe_wordpress_api, post_to_album_wordpress_api_fetch_posts_page, post_to_album_normalize_normalize_source_post, post_to_album_diff_build_enriched_post, post_to_album_diff_diff_post, post_to_album_wordpress_api_update_post [EXTRACTED 1.00]
- **Last.fm Enrichment + Confidence Flow** — post_to_album_cli__split_artist_album, post_to_album_cli__lastfm_enrichment, post_to_album_lastfm_fetch_lastfm_album_tags, post_to_album_lastfm_parse_lastfm_album_response, post_to_album_lastfm_normalize_lastfm_tags, post_to_album_lastfm_score_match [EXTRACTED 1.00]
- **Taxonomy Update Introspection Chain** — post_to_album_cli_main, post_to_album_cli__resolve_taxonomy_updates, post_to_album_wordpress_api_resolve_taxonomy_term_ids, post_to_album_wordpress_api_update_post, post_to_album_models_sourcepost [INFERRED 0.85]

## Communities (12 total, 1 thin omitted)

### Community 0 - "Normalization & Derivation"
Cohesion: 0.12
Nodes (38): Preserve Relisten and Unreleased, diff_post(), DiffResult, EnrichedPost, PreviousListen, SourcePost, Track, TrackSummary (+30 more)

### Community 1 - "WordPress API & Auth"
Cohesion: 0.13
Nodes (22): _auth_header helper, _resolve_taxonomy_updates helper, auth_header_for_apply(), build_basic_auth_header(), ensure_https(), fetch_posts_page(), probe_wordpress_api(), ProbeResult (+14 more)

### Community 2 - "Last.fm Enrichment"
Cohesion: 0.13
Nodes (24): Client, Confidence-gated Enrichment, LastfmMatch, _lastfm_enrichment helper, _split_artist_album helper, fetch_lastfm_album_tags(), lastfm_album_url(), LASTFM_API_URL Constant (+16 more)

### Community 3 - "Diff & Post Models"
Cohesion: 0.21
Nodes (22): build_enriched_post(), _full_durations(), listen_event_date_update(), match_metadata_update(), _optional_str(), release_date_update(), _rows(), spotify_album_url_update() (+14 more)

### Community 4 - "CLI Orchestration"
Cohesion: 0.27
Nodes (19): main(), LastfmAlbumMatch, test: dry-run records no writes, _probe_response(), _raw_post(), test_apply_emits_complete_payload_end_to_end_with_lastfm(), test_dry_run_message_lists_all_new_derived_fields(), test_main_apply_requires_credentials_before_network() (+11 more)

### Community 5 - "Spec & Plan"
Cohesion: 0.12
Nodes (16): Dry-run By Default, Idempotent Write Discipline, One-time Backfill Model, Transport Probe Gate, Execution Handoff, File Structure, Global Constraints, Self-Review (+8 more)

### Community 6 - "Config Loading"
Cohesion: 0.20
Nodes (9): Config, load_config(), format_dry_run_row(), summarize_counts(), test_load_config_defaults_to_dry_run(), test_load_config_rejects_empty_wordpress_base_url(), test_load_config_rejects_missing_wordpress_base_url(), test_format_dry_run_row_shows_post_id_reason_and_fields() (+1 more)

### Community 7 - "Reporting"
Cohesion: 0.05
Nodes (40): 1. Config layer, 1. Frontend archive filtering, 2. Frontend archive search and listing, 2. Source reader, 3. Canonical CMS dataset, 3. Enrichment engine, 4. Diff planner, 5. Writer (+32 more)

### Community 10 - "Community 10"
Cohesion: 0.20
Nodes (9): Apply, Confidence gate, Derived fields, Dry Run, Environment, Knowledge graph, Setup, Tests (+1 more)

### Community 11 - "Community 11"
Cohesion: 0.11
Nodes (17): Confidence gate, Derive deterministically from source post (always emit if source has the data), Derive from Last.fm (gated), Execution handoff, Field contract, File structure, Idempotence rule (diff planner), Open implementer notes (+9 more)

## Knowledge Gaps
- **71 isolated node(s):** `Response`, `Setup`, `Environment`, `Derived fields`, `Confidence gate` (+66 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `main()` connect `CLI Orchestration` to `Normalization & Derivation`, `WordPress API & Auth`, `Last.fm Enrichment`, `Diff & Post Models`, `Spec & Plan`, `Config Loading`?**
  _High betweenness centrality (0.311) - this node is a cross-community bridge._
- **Why does `WordPress Metadata Backfill Design` connect `Reporting` to `Spec & Plan`?**
  _High betweenness centrality (0.257) - this node is a cross-community bridge._
- **Why does `Dry-run By Default` connect `Spec & Plan` to `CLI Orchestration`?**
  _High betweenness centrality (0.132) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `SourcePost` (e.g. with `_resolve_taxonomy_updates helper` and `DiffResult`) actually correct?**
  _`SourcePost` has 13 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Response`, `Raised when WordPress rejects a write payload.`, `Setup` to the rest of the system?**
  _72 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Normalization & Derivation` be split into smaller, more focused modules?**
  _Cohesion score 0.11627906976744186 - nodes in this community are weakly interconnected._
- **Should `WordPress API & Auth` be split into smaller, more focused modules?**
  _Cohesion score 0.12681159420289856 - nodes in this community are weakly interconnected._