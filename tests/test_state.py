"""Tests for the review page store (state on Logseq pages), bootstrap, lock."""

from pathlib import Path

import pytest

from syndicator.nodes.backlink import read_hugo_hash
from syndicator.nodes.bootstrap import bootstrap
from syndicator.nodes.extract import scan_blog_posts, source_hash
from syndicator.nodes.hugo import write_bundle
from syndicator.state import (
    PipelineLock,
    ReviewState,
    ReviewStore,
    SocialPostState,
    caption_children,
    page_filename,
    parse_review_page,
    render_review_page,
    short_hash,
)

from conftest import FakeLLM, make_cfg


def make_post_block(channel: str = "facebook", index: int = 0, kind: str = "intro",
                    status: str = "draft", h: str = "abc123") -> SocialPostState:
    return SocialPostState(
        channel=channel,
        title="Intro" if kind == "intro" else f"Abschnitt {index}",
        status=status,  # type: ignore[arg-type]
        publishing_date="2026-06-15",
        source_hash=h,
        children=caption_children(
            f"Caption {channel} {index}\n\n#sailing",
            [f"../assets/syndicator/slug/{channel}/{index:02d}-x/img.jpg"],
            [],
        ),
    )


def test_store_roundtrip_and_channel_state(tmp_path: Path):
    store = ReviewStore(tmp_path / "pages")
    state = ReviewState(slug="2026-01-01_Test Post")
    state.posts = [
        make_post_block(index=0),
        make_post_block(index=1, kind="section"),
    ]
    store.save(state)

    page = tmp_path / "pages" / page_filename("2026-01-01_Test Post")
    assert page.exists()
    text = page.read_text(encoding="utf-8")
    assert text.startswith("- Facebook\n")
    assert "\t- Intro\n" in text
    assert "\t  status:: draft\n" in text
    assert "\t  publishing-date:: 2026-06-15\n" in text

    reloaded = store.load("2026-01-01_Test Post")
    assert reloaded.date == "2026-01-01"
    assert reloaded.title == "Test Post"
    assert len(reloaded.posts) == 2
    assert reloaded.posts_for("facebook")[0].children == state.posts[0].children
    assert reloaded.channel_state("facebook") == "draft"
    assert reloaded.channel_state("x") == "pending"  # no blocks, no override
    # Atomic writes leave no temp files.
    assert list((tmp_path / "pages").glob("*.tmp")) == []
    # Saving again with identical content keeps the file byte-identical.
    store.save(reloaded)
    assert page.read_text(encoding="utf-8") == text
    # all() finds the page again.
    assert [s.slug for s in store.all()] == ["2026-01-01_Test Post"]


def test_user_marks_posts_published_in_logseq(tmp_path: Path):
    """The manual review workflow: flip status:: directly on the page."""
    store = ReviewStore(tmp_path / "pages")
    state = ReviewState(slug="2026-01-01_T")
    state.posts = [make_post_block(index=0), make_post_block(index=1, kind="section")]
    store.save(state)
    page = store.path_for("2026-01-01_T")

    text = page.read_text(encoding="utf-8")
    page.write_text(text.replace("status:: draft", "status:: published", 1), encoding="utf-8")
    partial = store.load("2026-01-01_T")
    assert partial.posts_for("facebook")[0].status == "published"
    assert partial.channel_state("facebook") == "draft"  # one post still open

    page.write_text(
        text.replace("status:: draft", "status:: published"), encoding="utf-8"
    )
    assert store.load("2026-01-01_T").channel_state("facebook") == "published"


def test_parse_tolerates_logseq_mutations(tmp_path: Path):
    """Logseq adds id::/collapsed:: and users add notes — nothing may break."""
    text = (
        "- Facebook\n"
        "\t- Intro\n"
        "\t  id:: 69d91349-8bad-453e-8fb5-7f0d865881df\n"
        "\t  channel:: facebook\n"
        "\t  status:: Published\n"  # user typo: capital letter
        "\t  source-hash:: aaa\n"
        "\t  collapsed:: true\n"
        "\t\t- ```\n"
        "\t\t  Hello\n"
        "\t\t  ```\n"
        "\t\t- my own nested note\n"
    )
    state = parse_review_page("2026-01-01_T", text)
    post = state.posts_for("facebook")[0]
    assert post.status == "published"
    assert "id:: 69d91349-8bad-453e-8fb5-7f0d865881df" in post.extra_props
    assert "\t\t- my own nested note" in post.children

    # Round trip: unknown props and nested notes survive a rewrite.
    rendered = render_review_page(state)
    again = parse_review_page("2026-01-01_T", rendered)
    assert "id:: 69d91349-8bad-453e-8fb5-7f0d865881df" in again.posts[0].extra_props
    assert "\t\t- my own nested note" in again.posts[0].children
    assert render_review_page(again) == rendered


def test_parse_approved_and_scheduled_statuses():
    for status in ("approved", "scheduled"):
        text = (
            "- Facebook\n"
            "\t- Intro\n"
            "\t  channel:: facebook\n"
            f"\t  status:: {status}\n"
        )
        state = parse_review_page("2026-01-01_T", text)
        assert state.posts_for("facebook")[0].status == status
        assert state.channel_state("facebook") == "draft"


def test_stale_posts_and_replace_channel_posts():
    state = ReviewState(slug="2026-01-01_T")
    state.posts = [
        make_post_block(index=0, status="published", h="old1"),
        make_post_block(index=1, kind="section", status="draft", h="old1"),
    ]
    current = "sha256:" + "f" * 64
    assert [p.title for p in state.stale_posts("facebook", current)] == ["Abschnitt 1"]
    # Approved and scheduled blocks are never reported stale.
    for status in ("approved", "scheduled"):
        state.posts[1].status = status  # type: ignore[assignment]
        assert state.stale_posts("facebook", current) == []
    state.posts[1].status = "draft"
    # Fresh source hash clears staleness for draft posts.
    state.posts[1].source_hash = short_hash(current)
    assert state.stale_posts("facebook", current) == []

    state.replace_channel_posts("facebook", [make_post_block(index=0)])
    assert state.channel_state("facebook") == "draft"


def test_bootstrap_marks_hugo_and_renan(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}

    # Simulate the live site: render bundles for all posts except one (stale case:
    # Athen gets a bundle with modified content).
    for slug, post in posts.items():
        out = write_bundle(post, cfg.hugo_posts_dir, cfg, FakeLLM())
        if slug == "2026-06-03_Athen":
            idx = out / "index.de.md"
            idx.write_text(idx.read_text(encoding="utf-8") + "\nmanual drift\n", encoding="utf-8")
        else:
            # Simulate existing translations (all target languages).
            for lang in ("en", "de", "es", "fr", "it", "arrr"):
                if lang != post.lang_code:
                    (out / f"index.{lang}.md").write_text("dummy", encoding="utf-8")

    result = bootstrap(cfg)
    assert result.posts == len(posts)
    assert "2026-06-03_Athen" in result.hugo_stale
    assert "2026-05-19_Charly_Superstar" in result.hugo_in_sync

    store = ReviewStore(cfg.pages_dir)
    renan = store.load("2024-06-14_Renan")
    assert renan.channel_state("facebook") == "pending"

    charly = store.load("2026-05-19_Charly_Superstar")
    assert read_hugo_hash(posts["2026-05-19_Charly_Superstar"]) == short_hash(
        source_hash(posts["2026-05-19_Charly_Superstar"])
    )
    assert charly.channel_state("facebook") == "pending"

    athen = store.load("2026-06-03_Athen")
    assert read_hugo_hash(posts["2026-06-03_Athen"]) == ""  # stale -> regenerate on first run

    # The blog posts got their syndication:: backlink — without hash changes.
    rescanned = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    for slug, post in posts.items():
        assert "syndication:: [[syndicator/" in post.source_path.read_text(encoding="utf-8")
        assert source_hash(rescanned[slug]) == source_hash(post)


def test_bootstrap_is_idempotent_and_keeps_progress(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = scan_blog_posts(cfg.journals_dir, cfg.pages_dir)
    for post in posts:
        write_bundle(post, cfg.hugo_posts_dir, cfg, FakeLLM())

    bootstrap(cfg)
    store = ReviewStore(cfg.pages_dir)
    state = store.load("2026-05-19_Charly_Superstar")
    state.posts = [
        make_post_block(channel="facebook", status="published"),
        make_post_block(channel="instagram", status="published"),
    ]
    store.save(state)

    bootstrap(cfg)
    state = store.load("2026-05-19_Charly_Superstar")
    assert state.channel_state("facebook") == "published"
    assert state.channel_state("instagram") == "published"


def test_pipeline_lock(tmp_path: Path):
    lock_path = tmp_path / ".syndicator-lock.json"
    lock = PipelineLock(lock_path)
    with lock:
        assert lock_path.exists()
        # Same host can re-acquire (re-entrant for our purposes).
        assert lock.acquire()
    assert not lock_path.exists()


def test_pipeline_lock_blocks_other_host(tmp_path: Path, monkeypatch):
    lock_path = tmp_path / ".syndicator-lock.json"
    lock = PipelineLock(lock_path)
    assert lock.acquire()

    import syndicator.state as state_mod

    monkeypatch.setattr(state_mod.socket, "gethostname", lambda: "other-host")
    other = PipelineLock(lock_path)
    assert not other.acquire()
    with pytest.raises(RuntimeError):
        other.__enter__()
