"""hugo node: render a BlogPost into a Hugo leaf bundle.

Behavior-parity port of the old Go converter (main.go, processors.go,
writer.go): identical front matter, identical media handling (flattened
basenames, video/youtube shortcodes, featured image), identical bundle
directory naming.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from ..config import ChannelConfig, Config
from ..llm import LLMClient
from ..model import BlogPost, Meta
from .media_adapt import adapt_path_for_channel, channel_rewrites_filenames, output_basename

log = logging.getLogger(__name__)

# Same patterns as processors.go.
ASSET_RE = re.compile(r"!\[(.*?)\]\((.*?assets/)(.*?)\)(?:\{[^}]*\})?")
LOGSEQ_VIDEO_RE = re.compile(r"\{\{video\s+(https?://[^\s}]+)\s*\}\}")
YOUTUBE_ID_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]+)")

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".wmv", ".flv", ".webm", ".mkv", ".m4v", ".mpg", ".mpeg",
}

LANGUAGE_FILENAMES = {
    "german": "index.de.md",
    "english": "index.en.md",
    "spanish": "index.es.md",
    "french": "index.fr.md",
    "italian": "index.it.md",
}


def index_filename(language: str) -> str:
    return LANGUAGE_FILENAMES.get(language.strip().lower(), "index.de.md")


def escape_toml(s: str) -> str:
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    s = s.replace("\t", "\\t")
    return s


def front_matter(meta: Meta, summary: str) -> str:
    return (
        "+++\n"
        f'date = "{escape_toml(meta.date)}"\n'
        f'lastmod = "{escape_toml(meta.date)}"\n'
        "draft = false\n"
        f'title = "{escape_toml(meta.title)}"\n'
        f'summary = "{escape_toml(summary)}"\n'
        "[params]\n"
        f'  author = "{escape_toml(meta.author)}"\n'
        "+++\n\n"
    )


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


def render_index(post: BlogPost, ch: ChannelConfig | None = None) -> str:
    """Full index.<lang>.md content for the post's source language."""
    content = transform_content(build_content(post), ch)
    return front_matter(post.meta, summary_for(post)) + content + "\n"


def bundle_dir_name(post: BlogPost) -> str:
    return post.slug


def _write_channel_asset(
    src: Path,
    dest_name: str,
    out_dir: Path,
    cfg: Config,
    llm: LLMClient,
) -> None:
    """Adapt src for hugo into out_dir/dest_name, falling back to a raw copy on failure."""
    out = adapt_path_for_channel(src, "hugo", cfg, out_dir, llm, dest_name=dest_name)
    if out is None:
        log.warning("adapt failed for %s — copying original", src.name)
        dest = out_dir / dest_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)


def write_bundle(post: BlogPost, posts_dir: Path, cfg: Config, llm: LLMClient) -> Path:
    """Write the source-language bundle: index file, media, featured image."""
    out_dir = posts_dir / bundle_dir_name(post)
    out_dir.mkdir(parents=True, exist_ok=True)

    source_dir = post.source_path.parent
    raw_content = build_content(post)
    ch = cfg.shared.channels["hugo"]

    for src, name in collect_asset_copies(raw_content, source_dir):
        if not src.exists():
            log.warning("missing asset %s", src)
            continue
        dest_name = output_basename(name, ch)
        _write_channel_asset(src, dest_name, out_dir, cfg, llm)

    if post.meta.header:
        header_src = (source_dir / post.meta.header).resolve()
        if header_src.exists():
            featured_name = f"featured{header_src.suffix}"
            _write_channel_asset(header_src, featured_name, out_dir, cfg, llm)
        else:
            log.warning("missing header image %s", header_src)

    index_path = out_dir / index_filename(post.meta.language)
    index_path.write_text(render_index(post, ch), encoding="utf-8")
    return out_dir
