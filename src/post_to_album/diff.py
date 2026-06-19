from __future__ import annotations

from dataclasses import dataclass

from post_to_album.models import EnrichedPost, SourcePost


@dataclass(frozen=True, slots=True)
class DiffResult:
    changed: bool
    acf_updates: dict
    taxonomy_updates: dict[str, list[str]]
    reasons: list[str]


def diff_post(source: SourcePost, enriched: EnrichedPost) -> DiffResult:
    acf_updates = {
        key: value
        for key, value in enriched.acf_updates.items()
        if source.acf.get(key) != value
    }
    taxonomy_updates = {
        key: list(value)
        for key, value in enriched.taxonomy_updates.items()
        if value
    }
    changed = bool(acf_updates or taxonomy_updates)
    return DiffResult(
        changed=changed,
        acf_updates=acf_updates,
        taxonomy_updates=taxonomy_updates,
        reasons=["field-diff"] if changed else ["already-normalized"],
    )
