"""End-to-end tests for the social pipeline (fake LLM, no network)."""

from datetime import date
from pathlib import Path

from PIL import Image

from syndicator.model import Block, BlogPost, MediaRef, Meta
from syndicator.nodes.export import _referenced_dirs
from syndicator.nodes.extract import scan_blog_posts, source_hash
from syndicator.pipeline import next_catchup_post, run_social_for_post
from syndicator.state import ReviewStore, SocialPostState, caption_children, page_filename, short_hash

from conftest import FakeLLM, make_cfg


def create_real_assets(posts) -> None:
    """Create real (tiny) images/videos so media adaptation can run."""
    for post in posts:
        for media in post.all_media():
            if media.kind == "youtube" or media.source_path is None:
                continue
            media.source_path.parent.mkdir(parents=True, exist_ok=True)
            if media.source_path.exists():
                continue
            if media.kind == "image":
                Image.new("RGB", (1600, 900), (30, 90, 160)).save(media.source_path)
            else:
                # Keep "videos" as dummy files; ffmpeg handling is covered in
                # test_media_adapt. Here they will fail adaptation gracefully.
                media.source_path.write_bytes(b"not a real video")


def test_run_social_creates_review_page_and_media(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-06-10_Griechenland_❤️"]
    create_real_assets([post])

    page = run_social_for_post(cfg, post, llm=FakeLLM(), verify_links=False)
    assert page == cfg.pages_dir / page_filename(post.slug)
    text = page.read_text(encoding="utf-8")
    assert text.startswith("- Facebook\n")
    assert "- Facebook\n" in text and "- Instagram\n" in text and "- X\n" in text
    assert "[fake caption_facebook]" in text
    assert "location:: Corfu, Greece" in text

    store = ReviewStore(cfg.pages_dir)
    state = store.load(post.slug)
    # 2x5 (FB/IG) + 1x4 (X) section-derived posts, all draft.
    assert len(state.posts) == 14
    assert state.channel_state("facebook") == "draft"
    h = short_hash(source_hash(post))
    assert all(p.status == "draft" and p.source_hash == h for p in state.posts)

    intro = state.posts_for("facebook")[0]
    assert intro.title == "Intro"
    assert intro.publishing_date
    assert intro.location == "Corfu, Greece"
    assert state.posts_for("instagram")[0].location == "Corfu, Greece"
    assert state.posts_for("x")[0].location == ""

    # Facebook captions carry the inline blog link; Instagram (bio mode) not.
    fb_text = "\n".join(intro.children)
    assert "https://example.org/posts/" in fb_text
    ig_text = "\n".join(line for p in state.posts_for("instagram") for line in p.children)
    assert "https://example.org/posts/" not in ig_text

    # Adapted media live in assets/syndicator and are embedded relative to pages/.
    assert "](../assets/syndicator/" in fb_text
    ig_dir = cfg.social_assets_dir / post.slug / "instagram" / "02-gastfreundschaft-carousel"
    ig_images = list(ig_dir.glob("*.jpg"))
    assert ig_images
    with Image.open(ig_images[0]) as im:
        assert im.size == (720, 900)

    # The blog post got its backlink, without a source hash change.
    assert f"syndication:: [[syndicator/{post.slug}]]" in post.source_path.read_text(encoding="utf-8")
    rescanned = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    assert source_hash(rescanned[post.slug]) == source_hash(post)


def test_run_social_marks_channels_draft(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-05-19_Charly_Superstar"]
    create_real_assets([post])

    run_social_for_post(cfg, post, llm=FakeLLM(), verify_links=False)
    store = ReviewStore(cfg.pages_dir)
    state = store.load(post.slug)
    assert state.channel_state("facebook") == "draft"
    assert state.channel_state("instagram") == "draft"


def test_stale_drafts_regenerate_published_is_immutable(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    store = ReviewStore(cfg.pages_dir)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-05-19_Charly_Superstar"]
    create_real_assets([post])

    run_social_for_post(cfg, post, llm=FakeLLM(), verify_links=False)
    state = store.load(post.slug)
    h_before = state.posts_for("facebook")[0].source_hash

    # Fresh drafts: nothing to export.
    assert run_social_for_post(cfg, post, llm=FakeLLM(), verify_links=False) is None

    # X goes live entirely; the facebook intro block is published individually.
    state = store.load(post.slug)
    for p in state.posts_for("x"):
        p.status = "published"
    state.posts_for("facebook")[0].status = "published"
    frozen_children = list(state.posts_for("facebook")[0].children)
    store.save(state)
    fb_dir = cfg.social_assets_dir / post.slug / "facebook"
    frozen_sentinel = fb_dir / "00-intro" / "sentinel.txt"
    frozen_sentinel.parent.mkdir(parents=True, exist_ok=True)
    frozen_sentinel.write_text("keep", encoding="utf-8")
    draft_sentinel = fb_dir / "01-section" / "sentinel.txt"
    draft_sentinel.parent.mkdir(parents=True, exist_ok=True)
    draft_sentinel.write_text("replace", encoding="utf-8")

    # The post source changes -> stale drafts regenerate.
    post.blocks[0].raw += " edited"
    assert run_social_for_post(cfg, post, llm=FakeLLM(), verify_links=False) is not None

    state = store.load(post.slug)
    assert state.channel_state("x") == "published"  # untouched
    assert state.channel_state("facebook") == "draft"
    fb = state.posts_for("facebook")
    assert fb[0].status == "published"  # frozen block survived ...
    assert fb[0].children == frozen_children
    assert fb[0].source_hash == h_before  # ... including its old hash
    assert frozen_sentinel.exists()  # ... and its media directory
    assert fb[1].source_hash != h_before  # regenerated from the new source
    assert not draft_sentinel.exists()  # draft package dir was replaced
    assert all(p.source_hash == h_before for p in state.posts_for("x"))

    # force re-exports fresh drafts but still never touches published blocks.
    assert run_social_for_post(cfg, post, llm=FakeLLM(), verify_links=False, force=True) is not None
    state = store.load(post.slug)
    assert state.channel_state("x") == "published"
    assert state.posts_for("facebook")[0].children == frozen_children
    assert frozen_sentinel.exists()


def test_referenced_dirs_uses_second_to_last_segment():
    """Package dirs must be found even when the slug itself contains '/'."""
    posts = [
        SocialPostState(
            channel="facebook",
            title="Intro",
            children=caption_children(
                "Caption",
                [
                    "../assets/syndicator/2026-06-10_Trip/facebook/00-intro/img.jpg",
                    "../assets/syndicator/2026-06-10_Trogir/Split/facebook/01-title/img.jpg",
                ],
                [],
            ),
        ),
    ]
    assert _referenced_dirs(posts) == {"00-intro", "01-title"}


def test_export_with_slash_in_slug_keeps_referenced_media(tmp_path: Path):
    """A '/' in the post title must not confuse the package-dir cleanup —
    the old left-anchored regex captured the channel name and deleted
    every package dir of the channel, including published media."""
    cfg = make_cfg(tmp_path)
    img = tmp_path / "media" / "photo.jpg"
    img.parent.mkdir(parents=True)
    Image.new("RGB", (1600, 900), (30, 90, 160)).save(img)

    source_path = tmp_path / "journals" / "x.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "- type:: blog\n  date:: 2026-01-01\n  title:: Trogir/Split\n", encoding="utf-8"
    )
    post = BlogPost(
        meta=Meta(date="2026-01-01", title="Trogir/Split", language="german", status="online"),
        blocks=[
            Block(kind="text", raw="Intro."),
            Block(
                kind="media",
                raw=f"![photo.jpg]({img})",
                media=MediaRef(kind="image", source_path=img, filename="photo.jpg"),
            ),
            Block(kind="text", raw="Ein Abschnitt."),
        ],
        source_path=source_path,
    )
    assert post.slug == "2026-01-01_Trogir/Split"

    run_social_for_post(cfg, post, llm=FakeLLM(), verify_links=False, start=date(2026, 1, 2))
    store = ReviewStore(cfg.pages_dir)
    state = store.load(post.slug)
    assert state.posts_for("facebook")

    fb_dir = cfg.social_assets_dir / post.slug / "facebook"
    dirs_before = {p.name for p in fb_dir.iterdir() if p.is_dir()}
    assert dirs_before  # media packages were created

    # Freeze one block, then re-export: its package dir must survive.
    state.posts_for("facebook")[0].status = "published"
    store.save(state)
    run_social_for_post(
        cfg, post, llm=FakeLLM(), channels=["facebook"], verify_links=False, start=date(2026, 1, 2)
    )

    dirs_after = {p.name for p in fb_dir.iterdir() if p.is_dir()}
    assert dirs_before <= dirs_after


def test_catchup_order_and_state_transitions(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    store = ReviewStore(cfg.pages_dir)

    first = next_catchup_post(cfg, store)
    assert first is not None
    assert first.slug == "2024-06-14_Renan"  # oldest post first

    # Simulate Renan already published on all social channels.
    state = store.load(first.slug)
    for channel in ("facebook", "instagram", "x"):
        state.posts.append(
            SocialPostState(
                channel=channel,
                title="Intro",
                status="published",
            )
        )
    store.save(state)
    second = next_catchup_post(cfg, store)
    assert second.slug == "2026-01-17_Frühlingspläne_2026"

    # A fresh draft on one channel: the post still has pending channels left.
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    create_real_assets([posts[second.slug]])
    run_social_for_post(cfg, posts[second.slug], llm=FakeLLM(), verify_links=False, channels=["facebook"])
    assert store.load(second.slug).channel_state("facebook") == "draft"
    assert next_catchup_post(cfg, store).slug == second.slug

    # All channels draft -> the backlog moves on to the next post.
    run_social_for_post(cfg, posts[second.slug], llm=FakeLLM(), verify_links=False)
    third = next_catchup_post(cfg, store)
    assert third is not None
    assert third.slug != second.slug
