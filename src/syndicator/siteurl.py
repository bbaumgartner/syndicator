"""Compute post URLs on the Hugo site.

Hugo lowercases and sanitizes bundle directory names into URL paths
(MakePathSanitized). We approximate that rule to build the ``post_url`` that
ships in the webhook payloads. v2 does not verify liveness locally (there is no
review gate any more); the URL is computed and trusted.
"""

from __future__ import annotations

import unicodedata
from urllib.parse import quote

from .config import Config

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
