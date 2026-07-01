"""Tests for the social_plan node."""

from datetime import date
from pathlib import Path

from syndicator.model import Block, BlogPost, MediaRef, Meta
from syndicator.nodes.extract import scan_blog_posts
from syndicator.nodes.social_plan import plan_social

from conftest import create_dummy_assets, make_cfg


def griechenland(cfg):
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-06-10_Griechenland_❤️"]
    create_dummy_assets([post])
    return post


def test_plan_counts_and_dates(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    post = griechenland(cfg)
    plans = plan_social(post, cfg, start=date(2026, 6, 12))

    assert set(plans) == {"facebook", "instagram", "x"}
    for ch in ("facebook", "instagram"):
        intents = plans[ch]
        assert [i.kind for i in intents] == ["intro"] + ["section"] * 4
        assert [i.format for i in intents] == [
            "single",
            "reel",
            "carousel",
            "reel",
            "single",
        ]

    x = plans["x"]
    assert [i.kind for i in x] == ["intro"] + ["section"] * 3
    assert all(i.format == "single" for i in x)

    fb = plans["facebook"]
    assert [i.suggested_date for i in fb] == [
        "2026-06-12",
        "2026-06-14",
        "2026-06-17",
        "2026-06-19",
        "2026-06-21",
    ]
    assert fb[1].section_title == "Gastfreundschaft"
    assert fb[1].format == "reel"
    assert len(fb[1].media) == 1
    assert fb[1].media[0].kind == "video"
    assert fb[2].format == "carousel"
    assert len(fb[2].media) == 5  # 4 images + 1 video


def test_x_one_post_per_section_video_wins(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    post = griechenland(cfg)
    plans = plan_social(post, cfg, start=date(2026, 6, 12))

    assert len(plans["x"]) == 4  # intro + 3 sections

    gast = plans["x"][1]  # Gastfreundschaft has images and a video
    assert gast.format == "single"
    assert len(gast.media) == 1
    assert gast.media[0].kind == "video"

    herbst = plans["x"][3]  # Herbstpläne: images only -> capped at 4
    assert herbst.format == "single"
    assert all(m.kind == "image" for m in herbst.media)
    assert len(herbst.media) == 4


def test_instagram_header_fallback_for_text_only_section(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    header = tmp_path / "header.jpg"
    header.write_bytes(b"x")
    post = BlogPost(
        meta=Meta(date="2026-01-01", title="Test", header=str(header), language="german", status="online"),
        blocks=[
            Block(kind="text", raw="Intro."),
            Block(kind="text", raw="Nur Text, keine Medien."),
        ],
        source_path=tmp_path / "journals" / "x.md",
    )
    plans = plan_social(post, cfg)
    ig_section = plans["instagram"][1]
    assert len(ig_section.media) == 1
    assert ig_section.media[0].filename == "header.jpg"
    # Facebook/X get no media for a text-only section.
    assert plans["facebook"][1].media == []
    assert plans["x"][1].media == []


def test_video_only_section_yields_reels_on_ig_and_fb(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    post = BlogPost(
        meta=Meta(date="2026-01-01", title="Test", language="german", status="online"),
        blocks=[
            Block(kind="text", raw="Intro."),
            Block(
                kind="media",
                raw=f"![clip]({video})",
                media=MediaRef(kind="video", source_path=video, filename="clip.mp4"),
            ),
        ],
        source_path=tmp_path / "journals" / "x.md",
    )
    plans = plan_social(post, cfg)
    for ch in ("facebook", "instagram"):
        section_intents = plans[ch][1:]
        assert len(section_intents) == 1
        assert section_intents[0].format == "reel"
        assert len(section_intents[0].media) == 1

    x_section = plans["x"][1]
    assert x_section.format == "single"
    assert len(x_section.media) == 1
    assert x_section.media[0].kind == "video"


def test_plan_splits_sections_with_videos(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-06-10_Griechenland_❤️"]
    create_dummy_assets([post])
    plans = plan_social(post, cfg)
    assert len(plans["facebook"]) == 5
    assert len(plans["instagram"]) == 5
    assert len(plans["x"]) == 4


def test_missing_assets_are_excluded(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-05-19_Charly_Superstar"]  # no dummy assets created
    plans = plan_social(post, cfg)
    assert all(not i.media for i in plans["facebook"])
