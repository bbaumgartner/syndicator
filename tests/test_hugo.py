"""Parity tests for the hugo node.

The golden files were produced by the old Go converter
(logseq-to-hugo-converter) from the same sources; render_index() must match
byte for byte.
"""

from pathlib import Path

import pytest

from syndicator.nodes.extract import scan_blog_posts
from syndicator.nodes.hugo import bundle_dir_name, collect_asset_copies, build_content, render_index

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
