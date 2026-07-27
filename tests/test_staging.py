"""Tests for source staging."""

from pathlib import Path

from syndicator.extract import scan_blog_posts
from syndicator.trigger import stage_post

from conftest import create_dummy_assets, make_cfg


def test_stage_post_uploads_source_only(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    post = next(p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir) if p.header_media)
    create_dummy_assets([post])
    staged = stage_post(post, cfg, tmp_path / "work", include_social=True)
    assert staged.header_source is not None
    assert staged.header_source.startswith("header")
    assert staged.uploads
    assert all("/source/" in u.remote for u in staged.uploads)
    for sv in staged.videos:
        assert sv.source_filename
        assert "/" not in sv.source_filename
