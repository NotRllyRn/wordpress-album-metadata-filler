from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class ProbeResult:
    supports_acf_reads: bool
    supports_target_shape: bool
    sample_post_id: int | None


def probe_wordpress_api(base_url: str, auth_header: str | None, timeout_s: float) -> ProbeResult:
    headers = {"Authorization": auth_header} if auth_header else {}
    url = f"{base_url.rstrip('/')}/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta"
    response = httpx.get(url, headers=headers, timeout=timeout_s)
    response.raise_for_status()
    rows = response.json()
    if not rows:
        return ProbeResult(False, False, None)

    row = rows[0]
    acf = row.get("acf")
    has_acf = isinstance(acf, dict)
    return ProbeResult(
        supports_acf_reads=has_acf,
        supports_target_shape=has_acf and "music_tracks" in acf,
        sample_post_id=row.get("id"),
    )


def build_basic_auth_header(username: str, app_password: str) -> str:
    token = base64.b64encode(f"{username}:{app_password}".encode()).decode()
    return f"Basic {token}"


def fetch_posts_page(
    base_url: str,
    auth_header: str | None,
    page: int,
    per_page: int,
    timeout_s: float,
) -> list[dict]:
    headers = {"Authorization": auth_header} if auth_header else {}
    response = httpx.get(
        f"{base_url.rstrip('/')}/wp-json/wp/v2/posts",
        headers=headers,
        params={"page": page, "per_page": per_page, "_fields": "id,title,acf,meta,genre"},
        timeout=timeout_s,
    )
    if response.status_code == 400:
        body = response.json()
        if isinstance(body, dict) and body.get("code") == "rest_post_invalid_page_number":
            return []
    response.raise_for_status()
    return response.json()


def resolve_taxonomy_term_ids(
    base_url: str,
    auth_header: str,
    taxonomy: str,
    slugs: list[str],
    timeout_s: float,
) -> list[int]:
    term_ids: list[int] = []
    for slug in slugs:
        response = httpx.get(
            f"{base_url.rstrip('/')}/wp-json/wp/v2/{taxonomy}",
            headers={"Authorization": auth_header},
            params={"slug": slug, "_fields": "id,slug"},
            timeout=timeout_s,
        )
        response.raise_for_status()
        rows = response.json()
        if rows:
            term_ids.append(int(rows[0]["id"]))
    return term_ids


class WordpressWriteError(RuntimeError):
    """Raised when WordPress rejects a write payload."""


def _raise_on_bad_status(response: httpx.Response, payload: dict) -> None:
    if response.is_success:
        return
    try:
        body = response.json()
    except Exception:
        body = response.text
    raise WordpressWriteError(
        f"WordPress returned {response.status_code} for {response.url}\n"
        f"response body: {body}\n"
        f"payload: {payload}"
    )


def update_post(
    base_url: str,
    auth_header: str,
    post_id: int,
    acf_updates: dict,
    timeout_s: float,
    taxonomy_updates: dict[str, list[int]] | None = None,
) -> dict:
    payload: dict = {"acf": acf_updates}
    if taxonomy_updates:
        payload.update(taxonomy_updates)
    response = httpx.post(
        f"{base_url.rstrip('/')}/wp-json/wp/v2/posts/{post_id}",
        headers={"Authorization": auth_header},
        json=payload,
        timeout=timeout_s,
    )
    _raise_on_bad_status(response, payload)
    return response.json()
