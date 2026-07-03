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


def test_new_channel_gets_x_like_planning_from_config_alone(tmp_path: Path):
    """Extensibility scenario (architecture.md 10): a brand new platform is
    added by writing config only — no code change. A synthetic "mastodon"
    channel with ``video_exclusive`` set behaves exactly like X: one post
    per section (video wins, else images only, never mixed; no reel/carousel
    splitting), purely because of its config, not its name."""
    from syndicator.config import ChannelConfig
    from syndicator.nodes.caption import _enforce_text_budget, compose_post_text, text_budget
    from syndicator.model import PostIntent, SocialDraft

    cfg = make_cfg(tmp_path)
    cfg.shared.channels["mastodon"] = ChannelConfig(
        kind="social", max_media_per_post=4, max_chars=280, video_exclusive=True
    )
    post = griechenland(cfg)

    plans = plan_social(post, cfg, start=date(2026, 6, 12))
    assert "mastodon" in plans

    def shape(intents):
        return [(i.kind, i.format, [m.kind for m in i.media]) for i in intents]

    assert shape(plans["mastodon"]) == shape(plans["x"])

    mastodon_cfg = cfg.shared.channels["mastodon"]
    gast = plans["mastodon"][1]  # Gastfreundschaft has images and a video
    assert gast.format == "single"  # never split into reel/carousel
    assert len(gast.media) == 1 and gast.media[0].kind == "video"  # video wins, no mixing

    # Character-budget composition (LLM retry + tail-dropping) is triggered
    # by max_chars, independent of the channel name.
    budget = text_budget(mastodon_cfg)
    long_draft = SocialDraft(text="a" * 400, hashtags=["#one", "#two", "#three"])
    from conftest import FakeLLM

    fixed = _enforce_text_budget(long_draft, mastodon_cfg, "sys", "user", FakeLLM(), cfg, "mastodon")
    assert fixed.text == "[fake caption_mastodon]"  # real channel name reaches the LLM node

    url = "https://example.org/posts/mastodon/"
    composed = compose_post_text(
        SocialDraft(text="a" * budget, hashtags=["#sailing", "#mediterranean", "#travelcouple"]),
        PostIntent(channel="mastodon", index=0, kind="section"),
        mastodon_cfg,
        url,
        [],
    )
    effective = len(composed) - len(url) + 23
    assert effective <= 280
    assert "#travelcouple" not in composed  # trailing hashtag dropped like on X
