"""Parity tests for the hugo node.

The golden files were produced by the old Go converter
(logseq-to-hugo-converter) from the same sources; render_index() must match
byte for byte.
"""

from pathlib import Path

import pytest

from syndicator.nodes.extract import scan_blog_posts
from syndicator.nodes.hugo import (
    bundle_dir_name,
    bundle_filename,
    collect_asset_copies,
    build_content,
    render_index,
    transform_content,
    write_bundle,
)

from conftest import FakeLLM, make_cfg

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


def test_bundle_filename_maps_to_jpeg_and_mp4():
    assert bundle_filename("photo.png") == "photo.jpg"
    assert bundle_filename("clip.mov") == "clip.mp4"
    assert bundle_filename("already.jpg") == "already.jpg"


def test_transform_content_adapts_filenames():
    content = (
        '![a](../assets/Renan/foo.png) '
        '![b](../assets/Renan/bar.MOV)'
    )
    raw = transform_content(content, adapt_filenames=False)
    assert "foo.png" in raw
    assert '{{< video src="bar.MOV" >}}' in raw
    adapted = transform_content(content, adapt_filenames=True)
    assert "foo.jpg" in adapted
    assert '{{< video src="bar.mp4" >}}' in adapted


def test_write_bundle_adapts_portrait_images_to_landscape(tmp_path):
    from PIL import Image

    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2024-06-14_Renan"]

    for media in post.all_media():
        if media.kind != "image" or media.source_path is None:
            continue
        media.source_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (900, 1600), (80, 120, 160)).save(media.source_path)

    bundle = write_bundle(post, cfg.hugo_posts_dir, cfg, FakeLLM())
    with Image.open(bundle / "renand.jpg") as im:
        assert im.size == (900, 506)
    with Image.open(bundle / "featured.jpg") as im:
        assert im.size == (900, 506)
    index = (bundle / "index.en.md").read_text(encoding="utf-8")
    assert "renand.jpg" in index
    assert "quitschi.jpg" in index
