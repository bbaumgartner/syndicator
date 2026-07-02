"""Tests for live URL computation, verification and the RSS fallback."""

from pathlib import Path
from unittest.mock import patch

import httpx

from syndicator.siteurl import (
    _rss_lookup,
    hugo_path_segment,
    lang_prefix,
    post_url,
    resolve_post_url,
    url_is_live,
)

from conftest import make_cfg


def _rss(links: list[str]) -> str:
    items = "".join(f"<item><link>{link}</link></item>" for link in links)
    return f"<rss><channel>{items}</channel></rss>"


def test_lang_prefix_default_vs_other(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    assert lang_prefix(cfg, "en") == ""
    assert lang_prefix(cfg, "de") == "/de"


def test_resolve_without_verify_returns_computed(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    url = resolve_post_url(cfg, "2026-04-25_Törn", "en", verify=False)
    assert url == "https://example.org/posts/2026-04-25_t%C3%B6rn/"


def test_resolve_verified_live_uses_computed(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    with patch("syndicator.siteurl.url_is_live", return_value=True) as live:
        url = resolve_post_url(cfg, "2026-04-25_Törn", "en")
    assert url == post_url(cfg, "2026-04-25_Törn", "en")
    live.assert_called_once()


def test_resolve_falls_back_to_rss(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    real = "https://example.org/posts/2026-04-25_toern/"
    with (
        patch("syndicator.siteurl.url_is_live", return_value=False),
        patch("syndicator.siteurl._rss_lookup", return_value=real),
    ):
        assert resolve_post_url(cfg, "2026-04-25_Törn", "en") == real


def test_resolve_uses_computed_when_rss_fails(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    with (
        patch("syndicator.siteurl.url_is_live", return_value=False),
        patch("syndicator.siteurl._rss_lookup", return_value=None),
    ):
        assert resolve_post_url(cfg, "2026-04-25_Törn", "en") == post_url(cfg, "2026-04-25_Törn", "en")


def test_rss_lookup_prefers_exact_slug_over_date_prefix(tmp_path: Path):
    """Two posts on the same day: the date prefix alone is ambiguous."""
    cfg = make_cfg(tmp_path)
    links = [
        "https://example.org/posts/2026-06-10_athen/",
        "https://example.org/posts/2026-06-10_griechenland_%EF%B8%8F/",
    ]

    def fake_get(url, **kwargs):
        return httpx.Response(200, text=_rss(links), request=httpx.Request("GET", url))

    with patch("syndicator.siteurl.httpx.get", side_effect=fake_get):
        found = _rss_lookup(cfg, "2026-06-10_Griechenland_❤️", "en")
        assert found == links[1]
        # Unknown sanitization still falls back to the (first) date match.
        found = _rss_lookup(cfg, "2026-06-10_Written_Differently", "en")
        assert found == links[0]
        # No match at all.
        assert _rss_lookup(cfg, "2030-01-01_Nope", "en") is None


def test_rss_lookup_survives_http_errors(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    with patch(
        "syndicator.siteurl.httpx.get",
        side_effect=httpx.ConnectError("no network"),
    ):
        assert _rss_lookup(cfg, "2026-06-10_Griechenland_❤️", "en") is None


def test_url_is_live_handles_errors_and_status():
    ok = httpx.Response(200, request=httpx.Request("HEAD", "https://x/"))
    missing = httpx.Response(404, request=httpx.Request("HEAD", "https://x/"))
    with patch("syndicator.siteurl.httpx.head", return_value=ok):
        assert url_is_live("https://x/") is True
    with patch("syndicator.siteurl.httpx.head", return_value=missing):
        assert url_is_live("https://x/") is False
    with patch("syndicator.siteurl.httpx.head", side_effect=httpx.ConnectTimeout("t")):
        assert url_is_live("https://x/") is False


def test_hugo_path_segment_rules():
    assert hugo_path_segment("2026-04-25_Törn") == "2026-04-25_törn"
    assert hugo_path_segment("Hello World!") == "helloworld"
    assert hugo_path_segment("a-b.c_d") == "a-b.c_d"
