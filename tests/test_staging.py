"""Tests for reel/cover dedup grouping (pure planning logic)."""

from pathlib import Path

from syndicator.nodes.extract import scan_blog_posts
from syndicator.staging import _plan_reel_groups, _reel_group_dir, _reel_signature

from conftest import make_cfg


def _any_post(cfg):
    return scan_blog_posts(cfg.journals_dir, cfg.pages_dir)[0]


def test_short_source_shares_one_reel_across_platforms(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    post = _any_post(cfg)
    groups = _plan_reel_groups(post, cfg, duration=60.0)
    assert len(groups) == 1
    group = groups[0]
    assert group["spec_dir"] == "4x5"
    assert set(group["platforms"]) == {"facebook", "instagram", "x"}


def test_long_source_splits_instagram_trim(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    post = _any_post(cfg)
    groups = _plan_reel_groups(post, cfg, duration=120.0)
    # facebook (240) and x (140) stay untrimmed and share; instagram (90) diverges.
    by_dir = {g["spec_dir"]: set(g["platforms"]) for g in groups}
    assert by_dir == {"4x5": {"facebook", "x"}, "4x5-90s": {"instagram"}}


def test_reel_group_dir_naming(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    ig = cfg.shared.channels["instagram"].reel_video
    assert _reel_group_dir(ig, duration=60.0) == "4x5"      # untrimmed
    assert _reel_group_dir(ig, duration=120.0) == "4x5-90s"  # trimmed to 90


def test_reel_signature_distinguishes_trim(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    fb = cfg.shared.channels["facebook"].reel_video
    ig = cfg.shared.channels["instagram"].reel_video
    # Same geometry, both untrimmed at 60s -> identical signature (shared file).
    assert _reel_signature(fb, 60.0) == _reel_signature(ig, 60.0)
    # At 120s IG trims to 90 while FB does not -> different signatures.
    assert _reel_signature(fb, 120.0) != _reel_signature(ig, 120.0)
