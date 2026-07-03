"""Parity tests for the hugo node.

The golden files were produced by the old Go converter
(logseq-to-hugo-converter) from the same sources; render_index() must match
byte for byte.
"""

from pathlib import Path

import pytest

from syndicator.model import Meta
from syndicator.nodes.extract import scan_blog_posts
from syndicator.nodes.hugo import (
    bundle_dir_name,
    bundle_media_plan,
    collect_asset_copies,
    build_content,
    front_matter,
    render_index,
    transform_content,
    write_bundle,
)
from syndicator.nodes.media_adapt import adapt_or_copy, output_basename

from conftest import FakeLLM, create_dummy_assets, make_cfg

FIXTURES = Path(__file__).parent / "fixtures"

GOLDEN = {
    "2026-06-10_Griechenland_❤️": "index.de.md",
    "2026-05-19_Charly_Superstar": "index.de.md",
    "2026-06-03_Athen": "index.de.md",
    "2026-05-28_Lefkada": "index.de.md",
    "2026-01-17_Frühlingspläne_2026": "index.de.md",
    "2026-04-08_Segeln": "index.de.md",
    "2024-06-14_Renan": "index.en.md",
}


def all_posts():
    return {p.slug: p for p in scan_blog_posts(FIXTURES / "journals", FIXTURES / "pages")}


@pytest.mark.parametrize("slug", sorted(GOLDEN))
def test_render_index_matches_old_converter(slug):
    post = all_posts()[slug]
    golden_path = FIXTURES / "golden" / f"{slug}__{GOLDEN[slug]}"
    assert render_index(post) == golden_path.read_text(encoding="utf-8")


def test_front_matter_escapes_special_characters():
    """Quotes, backslashes and newlines in titles must not break the TOML."""
    meta = Meta(date="2026-01-01", title='Der "Superstar"', author="A\\B")
    fm = front_matter(meta, 'Zeile eins\nmit "Zitat"')
    assert 'title = "Der \\"Superstar\\""' in fm
    assert 'author = "A\\\\B"' in fm
    assert 'summary = "Zeile eins\\nmit \\"Zitat\\""' in fm


def test_bundle_dir_names():
    posts = all_posts()
    assert bundle_dir_name(posts["2026-06-10_Griechenland_❤️"]) == "2026-06-10_Griechenland_❤️"
    assert bundle_dir_name(posts["2024-06-14_Renan"]) == "2024-06-14_Renan"


def test_asset_copies_are_flattened():
    post = all_posts()["2024-06-14_Renan"]
    copies = collect_asset_copies(build_content(post), post.source_path.parent)
    assert copies, "Renan references assets"
    for src, name in copies:
        assert "/" not in name
        assert "assets" in str(src)


def test_output_basename_for_hugo_channel(tmp_path):
    cfg = make_cfg(tmp_path)
    ch = cfg.shared.channels["hugo"]
    assert output_basename("photo.png", ch) == "photo.png"
    assert output_basename("clip.mov", ch) == "clip.mp4"
    assert output_basename("already.jpg", ch) == "already.jpg"


def test_transform_content_adapts_filenames(tmp_path):
    cfg = make_cfg(tmp_path)
    ch = cfg.shared.channels["hugo"]
    content = (
        '![a](../assets/Renan/foo.png) '
        '![b](../assets/Renan/bar.MOV)'
    )
    raw = transform_content(content)
    assert "foo.png" in raw
    assert '{{< video src="bar.MOV" >}}' in raw
    adapted = transform_content(content, ch)
    assert "foo.png" in adapted
    assert '{{< video src="bar.mp4" >}}' in adapted


def _write_bundle_with_media(post, cfg, llm):
    """Mirror pipeline.run_site_for_post's composition: write_bundle + the
    media plan executed through media_adapt (adapt_or_copy)."""
    bundle = write_bundle(post, cfg.hugo_posts_dir, cfg)
    for src, dest_name in bundle_media_plan(post, cfg):
        adapt_or_copy(src, "hugo", cfg, bundle, llm, dest_name=dest_name)
    return bundle


def test_bundle_media_plan_pairs_content_assets_and_header(tmp_path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2024-06-14_Renan"]
    create_dummy_assets([post])
    source_dir = post.source_path.parent
    ch = cfg.shared.channels["hugo"]

    expected_content = [
        (src, output_basename(name, ch))
        for src, name in collect_asset_copies(build_content(post), source_dir)
        if src.exists()
    ]
    assert post.meta.header, "Renan fixture must set a header image"
    header_src = (source_dir / post.meta.header).resolve()
    assert header_src.exists(), "Renan fixture header image must exist"
    expected_header = (header_src, f"featured{header_src.suffix}")

    plan = bundle_media_plan(post, cfg)
    assert plan == [*expected_content, expected_header]


def test_write_bundle_copies_corrupt_image_as_fallback(tmp_path):
    """Unreadable media must not abort the bundle; the original is copied."""
    cfg = make_cfg(tmp_path)
    # Force convert mode so the corrupt file actually goes through Pillow.
    cfg.shared.channels["hugo"].image.mode = "convert"
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2024-06-14_Renan"]

    for media in post.all_media():
        if media.source_path is None:
            continue
        media.source_path.parent.mkdir(parents=True, exist_ok=True)
        media.source_path.write_bytes(b"not an image")

    bundle = _write_bundle_with_media(post, cfg, FakeLLM())
    assert (bundle / "index.en.md").exists()
    copied = bundle / "renand.jpg"
    assert copied.read_bytes() == b"not an image"


def test_write_bundle_keeps_images_unchanged(tmp_path):
    from PIL import Image

    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2024-06-14_Renan"]

    for media in post.all_media():
        if media.kind != "image" or media.source_path is None:
            continue
        media.source_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (900, 1600), (80, 120, 160)).save(media.source_path)

    bundle = _write_bundle_with_media(post, cfg, FakeLLM())
    with Image.open(bundle / "renand.jpg") as im:
        assert im.size == (900, 1600)
    with Image.open(bundle / "featured.jpg") as im:
        assert im.size == (900, 1600)
    index = (bundle / "index.en.md").read_text(encoding="utf-8")
    assert "renand.jpg" in index
    assert "quitschi.jpg" in index
