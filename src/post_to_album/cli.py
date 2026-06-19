from __future__ import annotations

import sys

import httpx

from post_to_album import enrich
from post_to_album.config import load_config
from post_to_album.diff import diff_post
from post_to_album.lastfm import (
    fetch_lastfm_album_tags,
    lastfm_album_url,
    match_source,
    split_artist_album,
)
from post_to_album.normalize import normalize_source_post
from post_to_album.report import format_dry_run_row, summarize_counts
from post_to_album.wordpress_api import (
    auth_header_for_apply,
    ensure_https,
    fetch_posts_page,
    probe_wordpress_api,
    resolve_taxonomies_for_post,
    update_post,
)


__all__ = [
    "fetch_lastfm_album_tags",
    "main",
]


def main(argv: list[str] | None = None) -> int:
    cfg = load_config(sys.argv[1:] if argv is None else argv)
    if cfg.apply:
        if not (cfg.wordpress_username and cfg.wordpress_app_password):
            raise ValueError("WORDPRESS_USERNAME and WORDPRESS_APP_PASSWORD are required with --apply")
        ensure_https(cfg.wordpress_base_url)

    auth_header = auth_header_for_apply(cfg.wordpress_username, cfg.wordpress_app_password) if cfg.apply else None
    probe = probe_wordpress_api(cfg.wordpress_base_url, auth_header, cfg.timeout_s)
    if not (probe.supports_acf_reads and probe.supports_target_shape):
        raise RuntimeError("WordPress API probe failed required shape checks")

    status_rows: list[tuple[str, int]] = []
    processed = 0
    page = 1
    with httpx.Client(timeout=cfg.timeout_s) as client:
        while cfg.limit is None or processed < cfg.limit:
            posts = fetch_posts_page(
                cfg.wordpress_base_url,
                auth_header,
                page=page,
                per_page=cfg.batch_size,
                timeout_s=cfg.timeout_s,
            )
            if not posts:
                break
            for raw in posts:
                if cfg.limit is not None and processed >= cfg.limit:
                    break
                source = normalize_source_post(raw)
                lastfm = match_source(source.title, source.acf.get("lastfm_release_id"), client, cfg.lastfm_api_key)
                if lastfm.confidence == "low":
                    print(format_dry_run_row(source.post_id, ["low-confidence-match"], {}))
                    status_rows.append(("low-confidence-skipped", source.post_id))
                    processed += 1
                    continue
                album_url = None
                if lastfm.release_id and " - " in source.title:
                    artist, album = split_artist_album(source.title)
                    album_url = lastfm_album_url(artist, album)
                enriched = enrich.build_enriched_post(
                    source,
                    genre_tags=lastfm.tags,
                    lastfm_release_id=lastfm.release_id,
                    lastfm_release_date=lastfm.release_date,
                    lastfm_lastfm_album_url=album_url,
                    lastfm_confidence=lastfm.confidence,
                    lastfm_score=lastfm.score,
                )
                diff = diff_post(source, enriched)
                taxonomy_term_ids = resolve_taxonomies_for_post(
                    cfg.wordpress_base_url,
                    auth_header or "",
                    raw,
                    diff.taxonomy_updates,
                    cfg.timeout_s,
                ) if cfg.apply else {}
                if not (diff.acf_updates or taxonomy_term_ids):
                    status_rows.append(("skipped", source.post_id))
                    processed += 1
                    continue
                print(format_dry_run_row(source.post_id, diff.reasons, diff.acf_updates))
                if cfg.apply:
                    update_post(
                        cfg.wordpress_base_url,
                        auth_header or "",
                        source.post_id,
                        diff.acf_updates,
                        cfg.timeout_s,
                        taxonomy_updates=taxonomy_term_ids,
                    )
                status_rows.append(("updated", source.post_id))
                processed += 1
            page += 1
    print(summarize_counts(status_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
