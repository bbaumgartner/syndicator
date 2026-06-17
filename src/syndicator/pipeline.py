"""Pipeline orchestration: wire nodes together, update state.

The social pipeline (plan -> caption -> media -> review page) runs
independently of the site pipeline (translate -> hugo -> journeymap -> git
push). All state lives on the per-post review pages inside the Logseq graph
(see state.py); the review itself happens in Logseq.
"""

from __future__ import annotations

import logging
from datetime import date

from .config import Config
from .llm import LLMClient
from .model import BlogPost
from .nodes.backlink import ensure_syndication_link
from .nodes.export import export_social
from .nodes.extract import scan_blog_posts, source_hash
from .state import PipelineLock, ReviewStore, blog_page_ref, now_iso, short_hash

log = logging.getLogger(__name__)


def make_llm(cfg: Config) -> LLMClient:
    return LLMClient(max_retries=cfg.shared.translate.max_retries)


def make_store(cfg: Config) -> ReviewStore:
    return ReviewStore(cfg.pages_dir)


def scan_posts(cfg: Config) -> list[BlogPost]:
    return scan_blog_posts(cfg.journals_dir, cfg.pages_dir)


def find_post(cfg: Config, slug: str) -> BlogPost:
    posts = {p.slug: p for p in scan_posts(cfg)}
    if slug not in posts:
        known = "\n  ".join(sorted(posts))
        raise SystemExit(f"unknown post slug: {slug}\nknown posts:\n  {known}")
    return posts[slug]


def stale_draft_channels(cfg: Config, store: ReviewStore, post: BlogPost) -> list[str]:
    """Draft channels with blocks generated from an older source version."""
    state = store.load(post.slug)
    h = source_hash(post)
    return [
        name
        for name in cfg.social_channels()
        if state.channel_state(name) == "draft" and state.stale_posts(name, h)
    ]


def social_channels_to_export(cfg: Config, store: ReviewStore, post: BlogPost) -> list[str]:
    """Channels needing an export: pending ones plus stale drafts.

    Published channels (every block published) are immutable — the posts are
    live on the platform and cannot be changed, so they are never re-exported
    (not even with force). Individual published blocks inside a draft channel
    are frozen by the export node.
    """
    state = store.load(post.slug)
    pending = [name for name in cfg.social_channels() if state.channel_state(name) == "pending"]
    return pending + stale_draft_channels(cfg, store, post)


def next_catchup_post(cfg: Config, store: ReviewStore) -> BlogPost | None:
    """Oldest post that still has social channels to export."""
    for post in scan_posts(cfg):  # sorted by date
        if social_channels_to_export(cfg, store, post):
            return post
    return None


def run_social_for_post(
    cfg: Config,
    post: BlogPost,
    llm: LLMClient | None = None,
    force: bool = False,
    verify_links: bool = True,
    start: date | None = None,
    channels: list[str] | None = None,
):
    """Generate social post blocks for one post on its review page.

    Default channel selection: pending plus stale drafts. ``force`` re-exports
    fresh drafts too. Published blocks are immutable and never regenerated.
    Returns the review page path, or None when there was nothing to do.
    """
    store = make_store(cfg)
    if channels is None:
        if force:
            state = store.load(post.slug)
            channels = [
                name for name in cfg.social_channels()
                if state.channel_state(name) != "published"
            ]
        else:
            channels = social_channels_to_export(cfg, store, post)
    if not channels:
        log.info("%s: no social channels to export (published is immutable)", post.slug)
        return None

    llm = llm or make_llm(cfg)
    page = export_social(
        post, cfg, llm, channels=channels, verify_links=verify_links, start=start
    )
    ensure_syndication_link(post)
    return page


# --- site pipeline ----------------------------------------------------------


def site_changed_posts(cfg: Config, store: ReviewStore) -> list[BlogPost]:
    """Posts whose content differs from what the hugo channel last processed."""
    changed = []
    for post in scan_posts(cfg):
        if store.load(post.slug).hugo_hash != short_hash(source_hash(post)):
            changed.append(post)
    return changed


def run_site_for_post(
    cfg: Config,
    post: BlogPost,
    llm: LLMClient,
    store: ReviewStore,
    try_run: bool = False,
    force: bool = False,
) -> bool:
    """Render the Hugo bundle and translations for one post.

    Returns True when the post was (re)generated. A try run does the real
    work (bundle + translations into the site repo working tree) but does
    not record the hugo state, so the next real run picks the post up again
    and commits (including re-translating).
    """
    from .nodes.hugo import write_bundle
    from .nodes.translate import translate_bundle

    h = short_hash(source_hash(post))
    state = store.load(post.slug)
    if not force and state.hugo_hash == h:
        return False

    bundle = write_bundle(post, cfg.hugo_posts_dir)
    log.info("%s: hugo bundle written (%s)", post.slug, bundle)

    translated = translate_bundle(post, cfg, llm, bundle)
    if translated:
        log.info("%s: translated to %s", post.slug, ", ".join(translated))

    if not try_run:
        # Record the processed hash here (not only after push): an identical
        # re-render produces no git diff, and the post must not be retried
        # forever. A failed push raises and leaves the state untouched.
        state = store.load(post.slug)
        state.blog_ref = blog_page_ref(post)
        state.hugo_hash = h
        store.save(state)
        ensure_syndication_link(post)
    return True


def run_all(
    cfg: Config,
    slugs: list[str] | None = None,
    try_run: bool = False,
    force: bool = False,
    site_only: bool = False,
    social_only: bool = False,
) -> None:
    """Full pipeline: site (hugo + translate + journeymap + git push) and the
    social exports for newly published posts.

    A try run does everything for real (LLM calls included) except the final
    git commit/push, so nothing goes live. Social blocks are exported too,
    without link verification: the slug-based post URLs only resolve once a
    real run pushes the site.
    """
    from .nodes.journeymap import generate_journey_map
    from .nodes.publish_git import commit_and_push, wait_for_deploy
    from .siteurl import post_url

    store = make_store(cfg)
    llm = make_llm(cfg)

    with PipelineLock(cfg.lock_path):
        if slugs:
            posts = [find_post(cfg, slug) for slug in slugs]
        else:
            posts = site_changed_posts(cfg, store) if not social_only else []

        new_posts: list[BlogPost] = []
        site_changed = False

        if not social_only:
            for post in posts:
                was_new = store.load(post.slug).hugo_status == "pending"
                if run_site_for_post(cfg, post, llm, store, try_run=try_run, force=force):
                    site_changed = True
                    if was_new:
                        new_posts.append(post)

            if site_changed:
                generate_journey_map(cfg)
                if try_run:
                    log.info(
                        "try run: skipping commit/push — inspect with: git -C %s status",
                        cfg.local.sailingnomads_dir,
                    )
                else:
                    pushed = commit_and_push(cfg)
                    if pushed:
                        for post in new_posts:
                            state = store.load(post.slug)
                            state.hugo_status = "published"
                            state.hugo_at = now_iso()
                            state.hugo_hash = short_hash(source_hash(post))
                            store.save(state)
                            url = post_url(cfg, post.slug, cfg.shared.site.default_language)
                            wait_for_deploy(cfg, url)
            else:
                log.info("site: nothing changed")

        if not site_only:
            # In a try run the post is not live yet, so skip link
            # verification; the URLs resolve once a real run pushes.
            verify = not try_run
            if slugs:
                for post in [find_post(cfg, s) for s in slugs]:
                    run_social_for_post(cfg, post, llm=llm, force=force, verify_links=verify)
            else:
                new_slugs = {p.slug for p in new_posts}
                for post in new_posts:
                    run_social_for_post(cfg, post, llm=llm, force=force, verify_links=verify)
                # Edited posts: re-export only stale drafts. Pending channels
                # of older posts stay in the manual catch-up backlog.
                for post in scan_posts(cfg):
                    if post.slug in new_slugs:
                        continue
                    stale = stale_draft_channels(cfg, store, post)
                    if stale:
                        run_social_for_post(
                            cfg, post, llm=llm, verify_links=verify, channels=stale
                        )
