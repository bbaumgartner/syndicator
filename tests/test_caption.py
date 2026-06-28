"""Tests for caption sanitization/assembly and URL computation (no network)."""

from pathlib import Path

from syndicator.model import MediaRef, PostIntent, SocialDraft
from syndicator.nodes.caption import (
    _caption_context,
    _sanitize,
    compose_post_text,
    generate_caption,
    x_text_budget,
)
from syndicator.nodes.extract import scan_blog_posts
from syndicator.nodes.social_plan import plan_social
from syndicator.siteurl import hugo_path_segment, post_url

from conftest import FakeLLM, create_dummy_assets, make_cfg


def intent_with_media(channel="facebook", n=2):
    media = [MediaRef(kind="image", alt=f"img{i}", filename=f"img{i}.jpg") for i in range(n)]
    return PostIntent(channel=channel, index=1, kind="section", section_index=0, media=media)


def test_sanitize_strips_urls_and_normalizes_hashtags():
    draft = SocialDraft(
        text="Look at this https://spam.example/x amazing place",
        hashtags=["sailing", "#travel", " #dog life ", ""],
        location="Corfu https://spam.example/x, Greece",
    )
    clean = _sanitize(draft)
    assert "https://" not in clean.text
    assert clean.hashtags == ["#sailing", "#travel", "#doglife"]
    assert "https://" not in clean.location
    assert clean.location == "Corfu  Greece"


def test_compose_inline_vs_bio():
    draft = SocialDraft(text="Hello sea", hashtags=["#sailing"])
    url = "https://example.org/posts/x/"

    fb_cfg = make_cfg_channel("facebook")
    fb = compose_post_text(draft, intent_with_media("facebook"), fb_cfg, url, ["https://youtu.be/abc"])
    assert fb == "Hello sea\n\nhttps://youtu.be/abc\n\nhttps://example.org/posts/x/\n\n#sailing"

    ig_cfg = make_cfg_channel("instagram")
    ig = compose_post_text(draft, intent_with_media("instagram"), ig_cfg, url, [])
    assert url not in ig
    assert ig == "Hello sea\n\nRead more by following the link in our bio.\n\n#sailing"

    x_cfg = make_cfg_channel("x")
    x = compose_post_text(draft, intent_with_media("x"), x_cfg, url, [])
    assert x == "Hello sea\n\n#sailing https://example.org/posts/x/"


def make_cfg_channel(name):
    from syndicator.config import ChannelConfig

    modes = {"facebook": "inline", "instagram": "bio", "x": "inline"}
    return ChannelConfig(kind="social", link_mode=modes[name], max_chars=280 if name == "x" else None)


def test_x_budget_enforcement():
    cfg_ch = make_cfg_channel("x")
    budget = x_text_budget(cfg_ch)
    assert budget == 280 - 25 - 25

    long_draft = SocialDraft(text="a" * 400, hashtags=[])
    from syndicator.nodes.caption import _enforce_x_budget

    # The LLM rewrite fits the budget -> used as-is.
    fixed = _enforce_x_budget(long_draft, cfg_ch, "sys", "user", FakeLLM())
    assert fixed.text == "[fake caption_x]"

    # Rewrite still too long -> hard truncation with ellipsis.
    class StillTooLongLLM(FakeLLM):
        def complete_structured(self, node, model, system, user_content, schema, temperature=None):
            return SocialDraft(text="b" * 400, hashtags=[])

    fixed = _enforce_x_budget(long_draft, cfg_ch, "sys", "user", StillTooLongLLM())
    assert len(fixed.text) <= budget
    assert fixed.text.endswith("…")


def test_caption_context_omits_other_section_text(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-06-10_Griechenland_❤️"]
    plans = plan_social(post, cfg)
    section_intent = plans["facebook"][2]  # Wirtschaftskrise
    ctx = _caption_context(post, section_intent)

    assert "outline" not in ctx
    assert "intro" not in ctx
    assert ctx["section_titles"] == ["Gastfreundschaft", "Wirtschaftskrise", "Herbstpläne"]
    assert ctx["write_about_this_part"]["title"] == "Wirtschaftskrise"
    assert "Wirtschaftskrise" in ctx["write_about_this_part"]["text"]
    assert "Gastfreundschaft" not in ctx["write_about_this_part"]["text"]
    assert "Herbstpläne" not in ctx["write_about_this_part"]["text"]
    assert ctx["position_hint"] == "40.13048,22.21514"


def test_generate_caption_full_flow(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-06-10_Griechenland_❤️"]
    create_dummy_assets([post])
    plans = plan_social(post, cfg)

    llm = FakeLLM()
    for channel, intents in plans.items():
        for intent in intents:
            draft = generate_caption(post, intent, cfg, llm)
            assert draft.text


def test_hugo_path_segment_and_post_url(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    assert hugo_path_segment("2026-04-25_Törn") == "2026-04-25_törn"
    assert hugo_path_segment("2026-06-10_Griechenland_❤️") == "2026-06-10_griechenland_\ufe0f"

    url = post_url(cfg, "2026-06-10_Griechenland_❤️", "en")
    assert url == "https://example.org/posts/2026-06-10_griechenland_%EF%B8%8F/"
    url_de = post_url(cfg, "2026-04-25_Törn", "de")
    assert url_de == "https://example.org/de/posts/2026-04-25_t%C3%B6rn/"
