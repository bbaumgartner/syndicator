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

from ..config import Config
from ..llm import LLMClient
from ..model import BlogPost, Meta
from .media_adapt import adapt_image, adapt_video, get_crop_focus

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


def hugo_adapts_media(cfg: Config) -> bool:
    """True when the hugo channel has image/video adaptation configured."""
    ch = cfg.shared.channels["hugo"]
    return bool(ch.image.width and ch.image.height) or bool(ch.video.width and ch.video.height)


def bundle_filename(original: str) -> str:
    """Output basename after hugo media adaptation (JPEG images, MP4 videos)."""
    stem = Path(original).stem
    if Path(original).suffix.lower() in VIDEO_EXTENSIONS:
        return f"{stem}.mp4"
    return f"{stem}.jpg"


def transform_content(content: str, *, adapt_filenames: bool = False) -> str:
    """Rewrite media references for the Hugo bundle (ProcessContent)."""

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
        if adapt_filenames:
            filename = bundle_filename(filename)
        if Path(filename).suffix.lower() in VIDEO_EXTENSIONS:
            return f'{{{{< video src="{filename}" >}}}}'
        return f"![{alt}]({filename})"

    return ASSET_RE.sub(replace_asset, content)


def render_index(post: BlogPost, *, adapt_filenames: bool = False) -> str:
    """Full index.<lang>.md content for the post's source language."""
    content = transform_content(build_content(post), adapt_filenames=adapt_filenames)
    return front_matter(post.meta, summary_for(post)) + content + "\n"


def bundle_dir_name(post: BlogPost) -> str:
    return post.slug


def _write_adapted_asset(
    src: Path,
    dest: Path,
    cfg: Config,
    llm: LLMClient,
) -> None:
    """Adapt src for hugo into dest, falling back to a raw copy on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ch_cfg = cfg.shared.channels["hugo"]
    try:
        if src.suffix.lower() in VIDEO_EXTENSIONS:
            focus = None
            if ch_cfg.video.width and ch_cfg.video.height and ch_cfg.video.pad_mode == "crop":
                focus = get_crop_focus(src, cfg, llm)
            adapt_video(src, ch_cfg.video, dest, focus)
        else:
            focus = None
            if ch_cfg.image.width and ch_cfg.image.height:
                focus = get_crop_focus(src, cfg, llm)
            adapt_image(src, ch_cfg.image, dest, focus)
    except Exception as err:  # noqa: BLE001 - fall back to a raw copy
        log.warning("adapt failed for %s (%s) — copying original", src.name, err)
        shutil.copyfile(src, dest)


def write_bundle(post: BlogPost, posts_dir: Path, cfg: Config, llm: LLMClient) -> Path:
    """Write the source-language bundle: index file, media, featured image."""
    out_dir = posts_dir / bundle_dir_name(post)
    out_dir.mkdir(parents=True, exist_ok=True)

    source_dir = post.source_path.parent
    raw_content = build_content(post)
    adapt = hugo_adapts_media(cfg)

    for src, name in collect_asset_copies(raw_content, source_dir):
        if not src.exists():
            log.warning("missing asset %s", src)
            continue
        dest = out_dir / (bundle_filename(name) if adapt else name)
        if adapt:
            _write_adapted_asset(src, dest, cfg, llm)
        else:
            shutil.copyfile(src, dest)

    if post.meta.header:
        header_src = (source_dir / post.meta.header).resolve()
        if header_src.exists():
            featured = out_dir / ("featured.jpg" if adapt else f"featured{header_src.suffix}")
            if adapt:
                _write_adapted_asset(header_src, featured, cfg, llm)
            else:
                shutil.copyfile(header_src, featured)
        else:
            log.warning("missing header image %s", header_src)

    index_path = out_dir / index_filename(post.meta.language)
    index_path.write_text(render_index(post, adapt_filenames=adapt), encoding="utf-8")
    return out_dir
