"""Pipeline orchestration: wire nodes together, update state.

The social pipeline (plan -> caption -> media -> export) runs independently
of the site pipeline (translate -> hugo -> journeymap -> git push), so the
catch-up phase works while the old converter still owns the website.
"""

from __future__ import annotations

import logging
from datetime import date

from .config import Config
from .llm import LLMClient
from .model import BlogPost
from .nodes.export import export_social
from .nodes.extract import scan_blog_posts, source_hash
from .state import PipelineLock, StateStore

log = logging.getLogger(__name__)


def make_llm(cfg: Config) -> LLMClient:
    return LLMClient(max_retries=cfg.shared.translate.max_retries)


def scan_posts(cfg: Config) -> list[BlogPost]:
    return scan_blog_posts(cfg.journals_dir, cfg.pages_dir)


def find_post(cfg: Config, slug: str) -> BlogPost:
    posts = {p.slug: p for p in scan_posts(cfg)}
    if slug not in posts:
        known = "\n  ".join(sorted(posts))
        raise SystemExit(f"unknown post slug: {slug}\nknown posts:\n  {known}")
    return posts[slug]


def stale_draft_channels(cfg: Config, store: StateStore, post: BlogPost) -> list[str]:
    """Draft channels whose package was made from an older source version."""
    state = store.load(post.slug)
    h = source_hash(post)
    return [
        name
        for name in cfg.social_channels()
        if state.channel(name).status == "draft" and state.channel(name).source_hash != h
    ]


def social_channels_to_export(cfg: Config, store: StateStore, post: BlogPost) -> list[str]:
    """Channels needing an export: pending ones plus stale drafts.

    Published channels are immutable — the post is live on the platform and
    cannot be changed, so they are never re-exported (not even with force).
    """
    state = store.load(post.slug)
    pending = [name for name in cfg.social_channels() if state.channel(name).status == "pending"]
    return pending + stale_draft_channels(cfg, store, post)


def next_catchup_post(cfg: Config, store: StateStore) -> BlogPost | None:
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
    """Generate social packages for one post and mark the channels as draft.

    Default channel selection: pending plus stale drafts. ``force`` re-exports
    fresh drafts too. Published channels are immutable and never re-exported.
    """
    store = StateStore(cfg.state_dir)
    if channels is None:
        if force:
            state = store.load(post.slug)
            channels = [
                name for name in cfg.social_channels()
                if state.channel(name).status != "published"
            ]
        else:
            channels = social_channels_to_export(cfg, store, post)
    if not channels:
        log.info("%s: no social channels to export (published is immutable)", post.slug)
        return None

    llm = llm or make_llm(cfg)
    export_dir = export_social(
        post, cfg, llm, channels=channels, verify_links=verify_links, start=start
    )

    h = source_hash(post)
    state = store.load(post.slug)
    state.title = post.meta.title
    state.date = post.meta.date
    state.source_hash = state.source_hash or h
    store.save(state)
    for channel in channels:
        store.mark(post.slug, channel, "draft", source_hash=h)

    return export_dir


# --- site pipeline ----------------------------------------------------------


def site_changed_posts(cfg: Config, store: StateStore) -> list[BlogPost]:
    """Posts whose content differs from what the hugo channel last processed."""
    changed = []
    for post in scan_posts(cfg):
        if store.load(post.slug).channel("hugo").source_hash != source_hash(post):
            changed.append(post)
    return changed


def run_site_for_post(
    cfg: Config,
    post: BlogPost,
    llm: LLMClient,
    store: StateStore,
    try_run: bool = False,
    force: bool = False,
) -> bool:
    """Render the Hugo bundle and translations for one post.

    Returns True when the post was (re)generated. A try run does the real
    work (bundle + translations into the site repo working tree) but does
    not record the hugo state, so the next real run picks the post up again
    and commits. Translations are cached either way; the cache also checks
    that the translated file still exists, so discarding the working tree
    simply re-translates next time.
    """
    from .nodes.hugo import write_bundle
    from .nodes.translate import translate_bundle

    h = source_hash(post)
    state = store.load(post.slug)
    if not force and state.channel("hugo").source_hash == h:
        return False

    bundle = write_bundle(post, cfg.hugo_posts_dir)
    log.info("%s: hugo bundle written (%s)", post.slug, bundle)

    translated = translate_bundle(post, cfg, llm, store, bundle, force=force)
    if translated:
        log.info("%s: translated to %s", post.slug, ", ".join(translated))

    if not try_run:
        # Record the processed hash here (not only after push): an identical
        # re-render produces no git diff, and the post must not be retried
        # forever. A failed push raises and leaves the state untouched.
        state = store.load(post.slug)
        state.title = post.meta.title
        state.date = post.meta.date
        state.source_hash = h
        state.channel("hugo").source_hash = h
        store.save(state)
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
    git commit/push, so nothing goes live. Social packages are exported too,
    without link verification: the slug-based post URLs only resolve once a
    real run pushes the site.
    """
    from .nodes.journeymap import generate_journey_map
    from .nodes.publish_git import commit_and_push, wait_for_deploy
    from .siteurl import post_url

    store = StateStore(cfg.state_dir)
    llm = make_llm(cfg)

    with PipelineLock(cfg.data_dir):
        if slugs:
            posts = [find_post(cfg, slug) for slug in slugs]
        else:
            posts = site_changed_posts(cfg, store) if not social_only else []

        new_posts: list[BlogPost] = []
        site_changed = False

        if not social_only:
            for post in posts:
                was_new = store.load(post.slug).channel("hugo").status == "pending"
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
                            store.mark(post.slug, "hugo", "published", source_hash=source_hash(post))
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
