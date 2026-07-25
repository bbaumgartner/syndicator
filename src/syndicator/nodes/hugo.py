"""hugo node (v2): the local media-rewriting helpers.

In v2 the Hugo *render* (front matter + index assembly + translation) and media
adaptation happen in n8n. What stays local is rewriting raw Logseq block text
into Hugo-oriented markdown (flattened source basenames, video/youtube
shortcodes). Hugo dest naming (videos → ``.mp4``) is owned by n8n Adapt Hugo
Media / Generate Hugo Index MDs; ``hugo_basename`` here mirrors that rule for
tests.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..model import VIDEO_EXTENSIONS, BlogPost

log = logging.getLogger(__name__)

# Same patterns as the old Go converter (processors.go).
ASSET_RE = re.compile(r"!\[(.*?)\]\((.*?assets/)(.*?)\)(?:\{[^}]*\})?")
LOGSEQ_VIDEO_RE = re.compile(r"\{\{video\s+(https?://[^\s}]+)\s*\}\}")
YOUTUBE_ID_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]+)")


def hugo_basename(original: str) -> str:
    """Mirror of n8n Hugo naming: images keep their name; videos become ``.mp4``."""
    path = Path(original)
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        return f"{path.stem}.mp4"
    return path.name


# Back-compat alias used by older tests/callers.
output_basename = hugo_basename


def build_content(post: BlogPost) -> str:
    """Join block raw texts with blank lines (buildContent in main.go)."""
    parts = [b.raw.strip() for b in post.blocks if b.raw.strip()]
    return "\n\n".join(parts)


def summary_for(post: BlogPost) -> str:
    if post.meta.summary:
        return post.meta.summary
    if post.blocks:
        return post.blocks[0].raw.replace("\n", " ")
    return ""


def collect_asset_copies(content: str, source_dir: Path) -> list[tuple[Path, str]]:
    """All (source_path, flattened_basename) pairs referenced in the content."""
    copies: list[tuple[Path, str]] = []
    for m in ASSET_RE.finditer(content):
        src = (source_dir / (m.group(2) + m.group(3))).resolve()
        copies.append((src, Path(m.group(3)).name))
    return copies


def transform_content(content: str) -> str:
    """Rewrite Logseq media refs to shortcodes using *source* basenames.

    Hugo dest renaming (``.mov`` → ``.mp4``) is applied later in n8n.
    """

    def replace_video_embed(m: re.Match[str]) -> str:
        url = m.group(1)
        yt = YOUTUBE_ID_RE.search(url)
        if yt:
            return f"{{{{< youtube {yt.group(1)} >}}}}"
        return m.group(0)

    content = LOGSEQ_VIDEO_RE.sub(replace_video_embed, content)

    def replace_asset(m: re.Match[str]) -> str:
        alt = m.group(1)
        filename = Path(m.group(3)).name
        if Path(filename).suffix.lower() in VIDEO_EXTENSIONS:
            return f'{{{{< video src="{filename}" >}}}}'
        return f"![{alt}]({filename})"

    return ASSET_RE.sub(replace_asset, content)


def bundle_media_plan(post: BlogPost) -> list[tuple[Path, str]]:
    """(source path, Hugo dest basename) pairs using the n8n naming mirror."""
    source_dir = post.source_path.parent
    plan: list[tuple[Path, str]] = []

    for src, name in collect_asset_copies(build_content(post), source_dir):
        if not src.exists():
            log.warning("missing asset %s", src)
            continue
        plan.append((src, hugo_basename(name)))

    if post.meta.header:
        header_src = (source_dir / post.meta.header).resolve()
        if header_src.exists():
            plan.append((header_src, f"featured{header_src.suffix}"))
        else:
            log.warning("missing header image %s", header_src)

    return plan
