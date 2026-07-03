"""Bootstrap comparison node: compare source renders with live Hugo bundles.

For one post, this node checks whether the live source-language bundle index
matches a fresh render and whether all configured translation files exist.
It returns the short source hash only when both checks pass; otherwise ``""``,
so the orchestrator can mark the post stale and regenerate it on first run.
"""

from __future__ import annotations

import logging

from ..config import Config
from ..hugo_format import index_filename
from ..model import BlogPost
from ..state import short_hash
from .extract import source_hash
from .hugo import render_index

log = logging.getLogger(__name__)


def hugo_bundle_hash(cfg: Config, post: BlogPost) -> str:
    """Return the source hash when the live bundle is fully in sync."""
    h = short_hash(source_hash(post))

    bundle = cfg.hugo_posts_dir / post.slug
    live_index = bundle / index_filename(post.meta.language)
    hugo_matches = False
    if live_index.exists():
        hugo_matches = live_index.read_text(encoding="utf-8") == render_index(
            post, cfg.shared.channels["hugo"]
        )

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
    return hugo_hash
