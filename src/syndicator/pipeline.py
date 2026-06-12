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


def make_llm(cfg: Config, dry_run: bool = False) -> LLMClient:
    return LLMClient(
        dry_run=dry_run,
        max_retries=cfg.shared.translate.max_retries,
    )


def scan_posts(cfg: Config) -> list[BlogPost]:
    return scan_blog_posts(cfg.journals_dir, cfg.pages_dir)


def find_post(cfg: Config, slug: str) -> BlogPost:
    posts = {p.slug: p for p in scan_posts(cfg)}
    if slug not in posts:
        known = "\n  ".join(sorted(posts))
        raise SystemExit(f"unknown post slug: {slug}\nknown posts:\n  {known}")
    return posts[slug]


def pending_social_channels(cfg: Config, store: StateStore, post: BlogPost) -> list[str]:
    state = store.load(post.slug)
    return [
        name
        for name in cfg.social_channels()
        if state.channel(name).status == "pending"
    ]


def next_catchup_post(cfg: Config, store: StateStore) -> BlogPost | None:
    """Oldest post that still has pending social channels."""
    for post in scan_posts(cfg):  # sorted by date
        if pending_social_channels(cfg, store, post):
            return post
    return None


def run_social_for_post(
    cfg: Config,
    post: BlogPost,
    dry_run: bool = False,
    force: bool = False,
    verify_links: bool = True,
    start: date | None = None,
):
    """Generate social packages for one post and mark channels as exported."""
    store = StateStore(cfg.state_dir)
    channels = list(cfg.social_channels()) if force else pending_social_channels(cfg, store, post)
    if not channels:
        log.info("%s: no pending social channels (use --force to re-export)", post.slug)
        return None

    llm = make_llm(cfg, dry_run=dry_run)
    export_dir = export_social(
        post, cfg, llm, channels=channels, verify_links=verify_links, start=start
    )

    h = source_hash(post)
    state = store.load(post.slug)
    state.title = post.meta.title
    state.date = post.meta.date
    state.source_hash = state.source_hash or h
    store.save(state)
    if not dry_run:
        for channel in channels:
            store.mark(post.slug, channel, "exported", source_hash=h)

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
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """Render the Hugo bundle and translations for one post.

    Returns True when the post was (re)generated. In dry-run mode bundles go
    to runs/dry-site/ instead of the real site repo.
    """
    from .nodes.hugo import write_bundle
    from .nodes.translate import translate_bundle

    h = source_hash(post)
    state = store.load(post.slug)
    if not force and state.channel("hugo").source_hash == h:
        return False

    if dry_run:
        posts_dir = cfg.try_run_output_dir / "dry-site" / "content" / "posts"
        posts_dir.mkdir(parents=True, exist_ok=True)
    else:
        posts_dir = cfg.hugo_posts_dir

    bundle = write_bundle(post, posts_dir)
    log.info("%s: hugo bundle written (%s)", post.slug, bundle)

    translated = translate_bundle(post, cfg, llm, store, bundle, force=force)
    if translated:
        log.info("%s: translated to %s", post.slug, ", ".join(translated))

    if not dry_run:
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
    dry_run: bool = False,
    force: bool = False,
    site_only: bool = False,
    social_only: bool = False,
) -> None:
    """Full pipeline: site (hugo + translate + journeymap + git push) and the
    social exports for newly published posts."""
    from .nodes.journeymap import generate_journey_map
    from .nodes.publish_git import commit_and_push, wait_for_deploy
    from .siteurl import post_url

    store = StateStore(cfg.state_dir)
    llm = make_llm(cfg, dry_run=dry_run)

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
                if run_site_for_post(cfg, post, llm, store, dry_run=dry_run, force=force):
                    site_changed = True
                    if was_new:
                        new_posts.append(post)

            if site_changed:
                generate_journey_map(cfg, dry_run=dry_run)
                pushed = commit_and_push(cfg, dry_run=dry_run)
                if pushed:
                    for post in new_posts:
                        store.mark(post.slug, "hugo", "published", source_hash=source_hash(post))
                        url = post_url(cfg, post.slug, cfg.shared.site.default_language)
                        wait_for_deploy(cfg, url)
            else:
                log.info("site: nothing changed")

        if not site_only:
            social_posts = new_posts if not slugs else [find_post(cfg, s) for s in slugs]
            for post in social_posts:
                run_social_for_post(cfg, post, dry_run=dry_run, force=force)
