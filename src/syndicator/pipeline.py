"""Pipeline orchestration: wire nodes together, update state.

The social pipeline (plan -> caption -> media -> export) runs independently
of the site pipeline (translate -> hugo -> journeymap -> git push), so the
catch-up phase works while the old converter still owns the website.
"""

from __future__ import annotations

import logging
from datetime import date

from .config import Config
from .llm import CostLedger, LLMClient
from .model import BlogPost
from .nodes.export import export_social
from .nodes.extract import scan_blog_posts, source_hash
from .state import StateStore

log = logging.getLogger(__name__)


def make_llm(cfg: Config, dry_run: bool = False) -> LLMClient:
    return LLMClient(
        ledger=CostLedger(prices=cfg.shared.model_prices),
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

    print(llm.ledger.summary())
    return export_dir
