"""Tests for the local media-rewriting helpers (v2 hugo node)."""

from pathlib import Path

from syndicator.nodes.extract import scan_blog_posts
from syndicator.nodes.hugo import (
    build_content,
    bundle_media_plan,
    collect_asset_copies,
    hugo_basename,
    transform_content,
)

from conftest import create_dummy_assets, make_cfg

FIXTURES = Path(__file__).parent / "fixtures"


def all_posts():
    return {p.slug: p for p in scan_blog_posts(FIXTURES / "journals", FIXTURES / "pages")}


def test_asset_copies_are_flattened():
    post = all_posts()["2024-06-14_Renan"]
    copies = collect_asset_copies(build_content(post), post.source_path.parent)
    assert copies, "Renan references assets"
    for src, name in copies:
        assert "/" not in name
        assert "assets" in str(src)


def test_hugo_basename():
    assert hugo_basename("photo.png") == "photo.png"
    assert hugo_basename("clip.mov") == "clip.mp4"
    assert hugo_basename("already.jpg") == "already.jpg"


def test_transform_content_keeps_source_filenames():
    content = "![a](../assets/Renan/foo.png) ![b](../assets/Renan/bar.MOV)"
    adapted = transform_content(content)
    assert "foo.png" in adapted
    assert '{{< video src="bar.MOV" >}}' in adapted


def test_transform_content_youtube_shortcode():
    content = "{{video https://youtu.be/FAIZtHHsbSM}}"
    assert transform_content(content) == "{{< youtube FAIZtHHsbSM >}}"


def test_bundle_media_plan_pairs_content_assets_and_header(tmp_path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2024-06-14_Renan"]
    create_dummy_assets([post])
    source_dir = post.source_path.parent

    expected_content = [
        (src, hugo_basename(name))
        for src, name in collect_asset_copies(build_content(post), source_dir)
        if src.exists()
    ]
    assert post.meta.header, "Renan fixture must set a header image"
    header_src = (source_dir / post.meta.header).resolve()
    assert header_src.exists(), "Renan fixture header image must exist"
    expected_header = (header_src, f"featured{header_src.suffix}")

    plan = bundle_media_plan(post)
    assert plan == [*expected_content, expected_header]
