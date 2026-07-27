"""Tests for the syndicated-at:: marker (journal + page formats)."""

from pathlib import Path

from syndicator.marker import is_syndicated, read_syndicated_at, set_syndicated_at
from syndicator.extract import scan_blog_posts

from conftest import make_cfg


def _posts(cfg):
    return {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}


def test_marker_roundtrip_journal_format(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    post = _posts(cfg)["2026-05-19_Charly_Superstar"]
    assert not is_syndicated(post)
    assert read_syndicated_at(post) == ""

    assert set_syndicated_at(post, "2026-07-18T12:00:00+02:00") is True
    assert read_syndicated_at(post) == "2026-07-18T12:00:00+02:00"
    assert is_syndicated(post)

    # A re-scan (fresh objects) still sees the marker; the block still parses.
    post2 = _posts(cfg)["2026-05-19_Charly_Superstar"]
    assert read_syndicated_at(post2) == "2026-07-18T12:00:00+02:00"


def test_marker_roundtrip_page_format(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    post = _posts(cfg)["2024-06-14_Renan"]
    assert set_syndicated_at(post, "2026-07-18T12:00:00+02:00") is True
    assert read_syndicated_at(post) == "2026-07-18T12:00:00+02:00"


def test_marker_is_idempotent_and_updatable(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    post = _posts(cfg)["2026-05-19_Charly_Superstar"]
    set_syndicated_at(post, "2026-07-18T12:00:00+02:00")
    # Same value: no rewrite.
    assert set_syndicated_at(post, "2026-07-18T12:00:00+02:00") is False
    # New value: updated in place, not duplicated.
    assert set_syndicated_at(post, "2026-07-19T09:00:00+02:00") is True
    text = post.source_path.read_text(encoding="utf-8")
    assert text.count("syndicated-at::") == 1
    assert read_syndicated_at(post) == "2026-07-19T09:00:00+02:00"


def test_marker_does_not_disturb_content(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    post = _posts(cfg)["2026-05-19_Charly_Superstar"]
    before_blocks = [b.raw for b in post.blocks]
    set_syndicated_at(post, "2026-07-18T12:00:00+02:00")
    after = _posts(cfg)["2026-05-19_Charly_Superstar"]
    assert [b.raw for b in after.blocks] == before_blocks
