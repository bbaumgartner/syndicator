"""hugo node (v2): the local media-rewriting helpers.

In v2 the Hugo *render* (front matter + index assembly + translation) happens
in n8n; the final site commit is manual. What stays local is the media
rewriting: turning raw Logseq block text into Hugo-ready markdown (flattened
bundle basenames, video/youtube shortcodes) and computing the bundle media
manifest that gets uploaded. This module keeps exactly that logic — the
``hugo`` channel is the site bundle spec.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..config import ChannelConfig, Config
from ..model import BlogPost
from .media_adapt import channel_rewrites_filenames, output_basename

log = logging.getLogger(__name__)

# Same patterns as the old Go converter (processors.go).
ASSET_RE = re.compile(r"!\[(.*?)\]\((.*?assets/)(.*?)\)(?:\{[^}]*\})?")
LOGSEQ_VIDEO_RE = re.compile(r"\{\{video\s+(https?://[^\s}]+)\s*\}\}")
YOUTUBE_ID_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]+)")

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".wmv", ".flv", ".webm", ".mkv", ".m4v", ".mpg", ".mpeg",
}


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


def transform_content(content: str, ch: ChannelConfig | None = None) -> str:
    """Rewrite media references for the Hugo bundle (ProcessContent)."""
    rewrite_filenames = ch is not None and channel_rewrites_filenames(ch)

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
        if rewrite_filenames and ch is not None:
            filename = output_basename(filename, ch)
        if Path(filename).suffix.lower() in VIDEO_EXTENSIONS:
            return f'{{{{< video src="{filename}" >}}}}'
        return f"![{alt}]({filename})"

    return ASSET_RE.sub(replace_asset, content)


def bundle_media_plan(post: BlogPost, cfg: Config) -> list[tuple[Path, str]]:
    """The full media copy plan for the bundle: (source path, dest basename) pairs.

    Covers every content asset referenced in the post plus, when set, the
    featured header image (as ``featured<ext>``). A missing source is logged and
    skipped, so callers only ever see files that exist. This is the authoritative
    media manifest for the site commit, independent of the block list.
    """
    source_dir = post.source_path.parent
    ch = cfg.shared.channels["hugo"]
    plan: list[tuple[Path, str]] = []

    for src, name in collect_asset_copies(build_content(post), source_dir):
        if not src.exists():
            log.warning("missing asset %s", src)
            continue
        plan.append((src, output_basename(name, ch)))

    if post.meta.header:
        header_src = (source_dir / post.meta.header).resolve()
        if header_src.exists():
            plan.append((header_src, f"featured{header_src.suffix}"))
        else:
            log.warning("missing header image %s", header_src)

    return plan
