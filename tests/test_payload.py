"""Tests for the /publish and /reel payload builders.

Includes a reconstruction-parity check: emitting the index from the structured
``blocks`` payload exactly as the n8n Code node will (front matter + verbatim
title/text raw + structured media) reproduces the golden index the old Go
converter produced, byte for byte — for every post without media-block artifacts
(v2 intentionally drops stray ``id::`` continuations, see Segeln below).
"""

from pathlib import Path

import pytest

from syndicator.nodes.extract import scan_blog_posts
from syndicator.payload import (
    build_blocks,
    build_meta,
    build_publish_payload,
    build_reel_payload,
    build_site_media,
)

from conftest import create_dummy_assets, make_cfg

FIXTURES = Path(__file__).parent / "fixtures"

GOLDEN = {
    "2026-06-10_Griechenland_❤️": "index.de.md",
    "2026-05-19_Charly_Superstar": "index.de.md",
    "2026-06-03_Athen": "index.de.md",
    "2026-05-28_Lefkada": "index.de.md",
    "2026-01-17_Frühlingspläne_2026": "index.de.md",
    "2024-06-14_Renan": "index.en.md",
    # Segeln excluded from parity: its golden retains an "id::" continuation on a
    # media block that the v2 structured emitter drops (verified below).
}


def _posts(cfg):
    return {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}


# --- reference n8n emitter (mirrors the Blog Post Publish Code node) ---------


def _escape_toml(s: str) -> str:
    for a, b in (("\\", "\\\\"), ('"', '\\"'), ("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")):
        s = s.replace(a, b)
    return s


def _front_matter(meta: dict) -> str:
    return (
        "+++\n"
        f'date = "{_escape_toml(meta["date"])}"\n'
        f'lastmod = "{_escape_toml(meta["date"])}"\n'
        "draft = false\n"
        f'title = "{_escape_toml(meta["title"])}"\n'
        f'summary = "{_escape_toml(meta["summary"])}"\n'
        "[params]\n"
        f'  author = "{_escape_toml(meta["author"])}"\n'
        "+++\n\n"
    )


def _emit_block(b: dict) -> str:
    if b["kind"] in ("title", "text"):
        return b["raw"]
    if b["kind"] == "youtube":
        return f'{{{{< youtube {b["media"]["youtube_id"]} >}}}}'
    m = b["media"]
    if m["kind"] == "video":
        return f'{{{{< video src="{m["bundle_filename"]}" >}}}}'
    return f'![{m["alt"]}]({m["bundle_filename"]})'


def _render_from_payload(post, cfg) -> str:
    meta = build_meta(post)
    body = "\n\n".join(_emit_block(b) for b in build_blocks(post, cfg))
    return _front_matter(meta) + body + "\n"


@pytest.mark.parametrize("slug", sorted(GOLDEN))
def test_reconstructed_index_matches_golden(slug, tmp_path):
    cfg = make_cfg(tmp_path)
    post = _posts(cfg)[slug]
    golden = FIXTURES / "golden" / f"{slug}__{GOLDEN[slug]}"
    assert _render_from_payload(post, cfg) == golden.read_text(encoding="utf-8")


def test_media_block_artifacts_are_dropped(tmp_path):
    """v2 emits media from structure, so stray id:: continuations disappear."""
    cfg = make_cfg(tmp_path)
    post = _posts(cfg)["2026-04-08_Segeln"]
    blocks = build_blocks(post, cfg)
    assert not any("id::" in (b.get("raw") or "") for b in blocks)
    videos = [b for b in blocks if b["kind"] == "media" and b["media"]["kind"] == "video"]
    assert any(b["media"]["bundle_filename"] == "charly-strand_1775833917832_0.mp4" for b in videos)


def test_build_blocks_youtube(tmp_path):
    cfg = make_cfg(tmp_path)
    post = _posts(cfg)["2026-06-03_Athen"]
    yt = [b for b in build_blocks(post, cfg) if b["kind"] == "youtube"]
    assert yt and yt[0]["media"]["youtube_id"] == "FAIZtHHsbSM"


def test_build_blocks_media_sftp_path(tmp_path):
    cfg = make_cfg(tmp_path)
    post = _posts(cfg)["2026-05-19_Charly_Superstar"]
    media = [b for b in build_blocks(post, cfg) if b["kind"] == "media"]
    assert media
    for b in media:
        m = b["media"]
        assert m["sftp_path"] == f"/syndicator/{post.slug}/site/{m['bundle_filename']}"


def test_build_site_media_includes_featured_and_journey_map(tmp_path):
    cfg = make_cfg(tmp_path)
    post = _posts(cfg)["2024-06-14_Renan"]
    create_dummy_assets([post])
    site_media = build_site_media(post, cfg, include_journey_map=True)
    names = [e.get("bundle_filename") for e in site_media]
    assert any(n and n.startswith("featured") for n in names)
    assert site_media[-1] == {
        "sftp_path": "/syndicator/journey-map.mp4",
        "repo_path": "static/journey-map.mp4",
    }
    # Without journey map, the last entry is a real bundle file.
    assert all(e.get("repo_path") is None for e in build_site_media(post, cfg, include_journey_map=False))


def test_build_publish_payload_shape(tmp_path):
    cfg = make_cfg(tmp_path)
    post = _posts(cfg)["2024-06-14_Renan"]
    create_dummy_assets([post])
    payload = build_publish_payload(
        post, cfg,
        site_media=build_site_media(post, cfg),
        header={"facebook": {"sftp_path": "/syndicator/x/header/facebook.jpg"}},
        redeploy=False,
    )
    assert payload["slug"] == post.slug
    assert payload["flags"] == {"redeploy": False}
    assert payload["meta"]["lang_code"] == "en"
    assert payload["post_url"].endswith(f"/posts/{post.slug.lower()}/")
    assert "blocks" in payload and "site_media" in payload


def test_build_reel_payload_shape(tmp_path):
    cfg = make_cfg(tmp_path)
    post = _posts(cfg)["2026-05-19_Charly_Superstar"]
    payload = build_reel_payload(
        post, cfg,
        index=1, section_title="Intro", section_text="Body text", alt="clip.mp4",
        reels={"facebook": "/syndicator/s/reels/4x5/1.mp4"},
        covers={"facebook": "/syndicator/s/covers/4x5/1.jpg"},
    )
    assert payload["video"] == {
        "index": 1, "section_title": "Intro", "section_text": "Body text", "alt": "clip.mp4",
    }
    assert payload["files"]["reels"]["facebook"].endswith("/reels/4x5/1.mp4")
    assert payload["post"]["lang_code"] == "de"
