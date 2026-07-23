# Superseded Plan

This root plan is retained only as historical context. It is **not** the implementation contract and must not be used as a canonical source.

The authoritative sequence is:

1. [`wordpress-album-metadata-overhaul-plans/00-overhaul-index.md`](wordpress-album-metadata-overhaul-plans/00-overhaul-index.md) — central contract, safety rules, and plan ownership.
2. [`wordpress-album-metadata-overhaul-plans/01-search-and-matching-plan.md`](wordpress-album-metadata-overhaul-plans/01-search-and-matching-plan.md) — provider search and identity matching.
3. [`wordpress-album-metadata-overhaul-plans/02-scf-and-wordpress-payload-plan.md`](wordpress-album-metadata-overhaul-plans/02-scf-and-wordpress-payload-plan.md) — approved SCF/taxonomy contract and WordPress payload semantics.
4. [`wordpress-album-metadata-overhaul-plans/03-planned-json-and-replay-plan.md`](wordpress-album-metadata-overhaul-plans/03-planned-json-and-replay-plan.md) — versioned plan artifact and replay.
5. [`wordpress-album-metadata-overhaul-plans/04-implementation-order-and-tests.md`](wordpress-album-metadata-overhaul-plans/04-implementation-order-and-tests.md) — final integration, documentation reconciliation, and rollout.

The July 23 SCF export referenced by the plan set is absent. Plan 02's explicit field/taxonomy list is the approved implementation contract, but the real deployed export must be checked before any live rollout. Never fabricate schema JSON.
