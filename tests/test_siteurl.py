"""Tests for post URL computation."""

from pathlib import Path

from syndicator.trigger import hugo_path_segment, lang_prefix, post_url

from conftest import make_cfg


def test_lang_prefix_default_vs_other(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    assert lang_prefix(cfg, "en") == ""
    assert lang_prefix(cfg, "de") == "/de"


def test_post_url_default_and_other_language(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    assert post_url(cfg, "2026-04-25_Törn", "en") == "https://example.org/posts/2026-04-25_t%C3%B6rn/"
    assert post_url(cfg, "2026-04-25_Törn", "de") == "https://example.org/de/posts/2026-04-25_t%C3%B6rn/"


def test_hugo_path_segment_rules():
    assert hugo_path_segment("2026-04-25_Törn") == "2026-04-25_törn"
    assert hugo_path_segment("Hello World!") == "helloworld"
    assert hugo_path_segment("a-b.c_d") == "a-b.c_d"
