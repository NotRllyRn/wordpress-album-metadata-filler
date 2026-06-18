from __future__ import annotations

import sys

from post_to_album.config import load_config
from post_to_album.diff import build_enriched_post, diff_post
import httpx

from post_to_album.lastfm import fetch_lastfm_album_tags, score_match
from post_to_album.normalize import normalize_source_post
from post_to_album.report import format_dry_run_row, summarize_counts
from post_to_album.wordpress_api import (
    build_basic_auth_header,
    fetch_posts_page,
    probe_wordpress_api,
    resolve_taxonomy_term_ids,
    update_post,
)


def main(argv: list[str] | None = None) -> int:
    cfg = load_config(sys.argv[1:] if argv is None else argv)
    if cfg.apply and not (cfg.wordpress_username and cfg.wordpress_app_password):
        raise ValueError("WORDPRESS_USERNAME and WORDPRESS_APP_PASSWORD are required with --apply")
    if cfg.apply and cfg.wordpress_base_url and not cfg.wordpress_base_url.startswith("https://"):
        raise ValueError("HTTPS required when sending credentials")

    auth_header = _auth_header(cfg.wordpress_username, cfg.wordpress_app_password) if cfg.apply else None
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
                genre_tags, confidence, score = _lastfm_enrichment(source, cfg.lastfm_api_key, client)
                diff = diff_post(source, build_enriched_post(source, genre_tags, confidence, score))
                taxonomy_updates = diff.taxonomy_updates
                if cfg.apply and diff.taxonomy_updates:
                    taxonomy_updates = _resolve_taxonomy_updates(
                        cfg.wordpress_base_url,
                        auth_header or "",
                        raw,
                        diff.taxonomy_updates,
                        cfg.timeout_s,
                    )
                if not (diff.acf_updates or taxonomy_updates):
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
                        taxonomy_updates=taxonomy_updates,
                    )
                status_rows.append(("updated", source.post_id))
                processed += 1
            page += 1
    print(summarize_counts(status_rows))
    return 0


def _auth_header(username: str | None, app_password: str | None) -> str | None:
    if not (username and app_password):
        return None
    return build_basic_auth_header(username, app_password)


def _lastfm_enrichment(source, api_key: str | None, client: httpx.Client) -> tuple[list[str], str, int]:
    artist, album = _split_artist_album(source.title)
    match = fetch_lastfm_album_tags(client, api_key, artist, album) if api_key and artist and album else None
    if match is None:
        confidence, score = score_match(
            source.acf.get("lastfm_release_id"),
            source.acf.get("lastfm_release_id"),
            True,
            True,
        )
        return [], confidence, score
    confidence, score = score_match(
        source.acf.get("lastfm_release_id"),
        match.release_id,
        artist.casefold() == match.artist.casefold(),
        album.casefold() == match.album.casefold(),
    )
    return match.tags, confidence, score


def _split_artist_album(title: str) -> tuple[str, str]:
    if " - " not in title:
        return "", title.strip()
    artist, album = title.split(" - ", 1)
    return artist.strip(), album.strip()


def _resolve_taxonomy_updates(
    base_url: str,
    auth_header: str,
    raw_post: dict,
    taxonomy_updates: dict[str, list[str]],
    timeout_s: float,
) -> dict[str, list[int]]:
    resolved: dict[str, list[int]] = {}
    for taxonomy, slugs in taxonomy_updates.items():
        ids = resolve_taxonomy_term_ids(base_url, auth_header, taxonomy, slugs, timeout_s)
        current_ids = [int(value) for value in raw_post.get(taxonomy, [])]
        if ids and ids != current_ids:
            resolved[taxonomy] = ids
    return resolved


if __name__ == "__main__":
    raise SystemExit(main())
