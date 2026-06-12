"""Compute and verify live post URLs on the Hugo site.

Hugo lowercases and sanitizes bundle directory names into URL paths
(MakePathSanitized). We approximate that rule, verify with a HEAD request
and fall back to scanning the RSS feed when the guess is wrong.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from urllib.parse import quote, unquote

import httpx

from .config import Config

log = logging.getLogger(__name__)

_KEEP_CATEGORIES = ("L", "N", "M")  # letters, numbers, marks (e.g. U+FE0F)
_KEEP_CHARS = set("-._")


def hugo_path_segment(name: str) -> str:
    """Approximate Hugo's MakePathSanitized + lowercase for one path segment."""
    out = []
    for ch in name:
        if ch in _KEEP_CHARS or unicodedata.category(ch)[0] in _KEEP_CATEGORIES:
            out.append(ch.lower())
    return "".join(out)


def lang_prefix(cfg: Config, lang: str) -> str:
    return "" if lang == cfg.shared.site.default_language else f"/{lang}"


def post_url(cfg: Config, slug: str, lang: str) -> str:
    segment = quote(hugo_path_segment(slug))
    return f"{cfg.shared.site.base_url}{lang_prefix(cfg, lang)}/posts/{segment}/"


def _rss_lookup(cfg: Config, slug: str, lang: str) -> str | None:
    feed_url = f"{cfg.shared.site.base_url}{lang_prefix(cfg, lang)}/index.xml"
    try:
        resp = httpx.get(feed_url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as err:
        log.warning("RSS lookup failed (%s): %s", feed_url, err)
        return None

    date_part = slug.split("_", 1)[0]
    for link in re.findall(r"<link>([^<]+)</link>", resp.text):
        if f"/posts/{date_part}_" in unquote(link) or f"/posts/{date_part}_" in link:
            return link
    return None


def url_is_live(url: str) -> bool:
    try:
        resp = httpx.head(url, timeout=20, follow_redirects=True)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def resolve_post_url(cfg: Config, slug: str, lang: str, verify: bool = True) -> str:
    candidate = post_url(cfg, slug, lang)
    if not verify:
        return candidate
    if url_is_live(candidate):
        return candidate
    from_rss = _rss_lookup(cfg, slug, lang)
    if from_rss:
        return from_rss
    log.warning("could not verify live URL for %s (%s) — using computed URL", slug, candidate)
    return candidate
