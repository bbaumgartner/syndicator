"""bootstrap node: create review pages with initial state for existing posts.

- hugo: everything that is live on sailingnomads.ch counts as published.
  The recorded source hash is only set when a fresh render matches the live
  bundle byte for byte; otherwise the post is considered stale and the first
  pipeline run regenerates (and re-translates) it.
- social channels: status lives on per-post blocks (``status::`` on each block).
  Posts without blocks stay pending and form the catch-up backlog.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import Config
from ..model import BlogPost
from ..state import ReviewStore, short_hash
from .backlink import ensure_syndication_link, set_hugo_hash
from .extract import scan_blog_posts, source_hash
from .hugo import index_filename, render_index

log = logging.getLogger(__name__)


@dataclass
class BootstrapResult:
    posts: int = 0
    hugo_in_sync: list[str] = field(default_factory=list)
    hugo_stale: list[str] = field(default_factory=list)


def bootstrap(cfg: Config) -> BootstrapResult:
    store = ReviewStore(cfg.pages_dir)
    posts = scan_blog_posts(cfg.journals_dir, cfg.pages_dir)
    result = BootstrapResult(posts=len(posts))

    for post in posts:
        hugo_hash = _bootstrap_post(cfg, store, post)
        if hugo_hash:
            result.hugo_in_sync.append(post.slug)
        else:
            result.hugo_stale.append(post.slug)

    return result


def _bootstrap_post(cfg: Config, store: ReviewStore, post: BlogPost) -> str:
    h = short_hash(source_hash(post))
    state = store.load(post.slug)

    bundle = cfg.hugo_posts_dir / post.slug
    live_index = bundle / index_filename(post.meta.language)
    hugo_matches = False
    if live_index.exists():
        hugo_matches = live_index.read_text(encoding="utf-8") == render_index(post)

    translations_complete = all(
        (bundle / f"index.{lang}.md").exists()
        for lang in cfg.shared.languages.supported
        if lang != post.lang_code
    )
    hugo_hash = h if hugo_matches and translations_complete else ""
    if not hugo_matches:
        log.info("hugo bundle stale or missing for %s — will be regenerated on first run", post.slug)
    elif not translations_complete:
        log.info("translations incomplete for %s — will be regenerated on first run", post.slug)

    store.save(state)
    set_hugo_hash(post, hugo_hash)
    ensure_syndication_link(post)
    return hugo_hash
