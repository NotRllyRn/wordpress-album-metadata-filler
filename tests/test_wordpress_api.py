import json

from post_to_album.wordpress_api import (
    build_basic_auth_header,
    fetch_posts_page,
    probe_wordpress_api,
    resolve_taxonomy_term_ids,
    update_post,
)


def test_probe_wordpress_api_detects_acf_payload(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://example.com/wp-json/wp/v2/posts?per_page=1&_fields=id,acf,meta",
        json=[{"id": 1, "acf": {"music_tracks": []}, "meta": {}}],
    )

    result = probe_wordpress_api("https://example.com", None, 5.0)

    assert result.supports_acf_reads is True
    assert result.supports_target_shape is True
    assert result.sample_post_id == 1


def test_build_basic_auth_header_has_basic_prefix():
    header = build_basic_auth_header("user", "app-pass")

    assert header.startswith("Basic ")


def test_fetch_posts_page_requests_acf_fields(httpx_mock):
    httpx_mock.add_response(json=[{"id": 1, "acf": {}}])

    rows = fetch_posts_page("https://example.com/", "Basic token", page=2, per_page=5, timeout_s=3)

    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Basic token"
    assert request.url == "https://example.com/wp-json/wp/v2/posts?page=2&per_page=5&_fields=id%2Ctitle%2Cacf%2Cmeta%2Cgenre"
    assert rows == [{"id": 1, "acf": {}}]


def test_resolve_taxonomy_term_ids_looks_up_slugs(httpx_mock):
    httpx_mock.add_response(json=[{"id": 7, "slug": "dream-pop"}])

    result = resolve_taxonomy_term_ids("https://example.com/", "Basic token", "genre", ["dream-pop"], 3)

    request = httpx_mock.get_request()
    assert request.url == "https://example.com/wp-json/wp/v2/genre?slug=dream-pop&_fields=id%2Cslug"
    assert request.headers["Authorization"] == "Basic token"
    assert result == [7]


def test_update_post_sends_acf_payload(httpx_mock):
    httpx_mock.add_response(json={"id": 10, "acf": {"music_total_tracks": 3}})

    result = update_post(
        "https://example.com/",
        "Basic token",
        10,
        {"music_total_tracks": 3},
        timeout_s=3,
    )

    request = httpx_mock.get_request()
    assert request.method == "POST"
    assert request.url == "https://example.com/wp-json/wp/v2/posts/10"
    assert request.headers["Authorization"] == "Basic token"
    assert json.loads(request.read()) == {"acf": {"music_total_tracks": 3}}
    assert result["id"] == 10


def test_update_post_sends_taxonomy_ids_when_present(httpx_mock):
    httpx_mock.add_response(json={"id": 10, "genre": [7]})

    update_post(
        "https://example.com/",
        "Basic token",
        10,
        {"music_mood_tags": ["dream-pop"]},
        timeout_s=3,
        taxonomy_updates={"genre": [7]},
    )

    assert json.loads(httpx_mock.get_request().read()) == {
        "acf": {"music_mood_tags": ["dream-pop"]},
        "genre": [7],
    }
