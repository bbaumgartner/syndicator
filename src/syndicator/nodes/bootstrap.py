"""bootstrap node: initialize per-channel state for existing posts.

- hugo: everything that is live on sailingnomads.ch counts as published.
  The recorded source hash is only set when a fresh render matches the live
  bundle byte for byte; otherwise the post is considered stale and the first
  pipeline run regenerates (and re-translates) it.
- social/article channels: only explicitly listed slugs (default: Renan, the
  only post ever cross-posted) count as published — everything else stays
  pending and forms the catch-up backlog.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import ALL_CHANNELS, Config
from ..model import BlogPost
from ..state import StateStore, now_iso
from .extract import scan_blog_posts, source_hash
from .hugo import index_filename, render_index

log = logging.getLogger(__name__)

# The only post that was ever published on social media / Substack / Medium.
DEFAULT_SOCIAL_PUBLISHED_SLUGS = ["2024-06-14_Renan"]


@dataclass
class BootstrapResult:
    posts: int = 0
    hugo_in_sync: list[str] = field(default_factory=list)
    hugo_stale: list[str] = field(default_factory=list)
    social_published: list[str] = field(default_factory=list)


def bootstrap(cfg: Config, social_published_slugs: list[str] | None = None) -> BootstrapResult:
    published_slugs = social_published_slugs or DEFAULT_SOCIAL_PUBLISHED_SLUGS
    store = StateStore(cfg.state_dir)
    posts = scan_blog_posts(cfg.journals_dir, cfg.pages_dir)
    result = BootstrapResult(posts=len(posts))

    for post in posts:
        result_state = _bootstrap_post(cfg, store, post, published_slugs)
        if result_state.channel("hugo").source_hash:
            result.hugo_in_sync.append(post.slug)
        else:
            result.hugo_stale.append(post.slug)
        if post.slug in published_slugs:
            result.social_published.append(post.slug)

    return result


def _bootstrap_post(cfg: Config, store: StateStore, post: BlogPost, published_slugs: list[str]):
    h = source_hash(post)
    state = store.load(post.slug)
    state.title = post.meta.title
    state.date = post.meta.date
    state.source_hash = h

    bundle = cfg.hugo_posts_dir / post.slug
    live_index = bundle / index_filename(post.meta.language)
    hugo_matches = False
    if live_index.exists():
        hugo_matches = live_index.read_text(encoding="utf-8") == render_index(post)

    hugo = state.channel("hugo")
    hugo.status = "published"
    hugo.at = hugo.at or now_iso()
    hugo.source_hash = h if hugo_matches else ""
    if not hugo_matches:
        log.info("hugo bundle stale or missing for %s — will be regenerated on first run", post.slug)

    # Existing translations only count when the source-language render is in sync.
    if hugo_matches:
        for lang in cfg.shared.languages.supported:
            if lang == post.lang_code:
                continue
            if (bundle / f"index.{lang}.md").exists():
                state.translations.setdefault(lang, h)

    for name in ALL_CHANNELS:
        if name == "hugo":
            continue
        ch = state.channel(name)
        if ch.status == "pending" and post.slug in published_slugs:
            ch.status = "published"
            ch.at = now_iso()
            ch.source_hash = h

    store.save(state)
    return state
