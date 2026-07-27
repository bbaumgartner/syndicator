"""Parse Logseq markdown into BlogPost objects (journal + page formats)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

LANGUAGE_WORD_TO_CODE = {
    "german": "de",
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "italian": "it",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".wmv", ".flv", ".webm", ".mkv", ".m4v", ".mpg", ".mpeg",
}

REEL_VIDEO_MARKER = "[VIDEO]"

MediaKind = Literal["image", "video", "youtube"]
BlockKind = Literal["title", "media", "youtube", "text"]

BULLET_RE = re.compile(r"^(\t*)-(?: (.*))?$")
PROP_RE = re.compile(r"(\w+)::\s*(.*)")
ROOT_PROP_RE = re.compile(r"^\w+::\s*")
MEDIA_FIRST_LINE_RE = re.compile(r"^!\[(.*?)\]\(([^)]*?)\)(?:\{[^}]*\})?\s*$")
YOUTUBE_FIRST_LINE_RE = re.compile(r"^\{\{video\s+(https?://[^\s}]+)\s*\}\}\s*$")
YOUTUBE_ID_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]+)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
PAREN_PATH_RE = re.compile(r"\((.*?)\)")
INLINE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
INLINE_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")


@dataclass
class Meta:
    date: str = ""
    title: str = ""
    author: str = ""
    header: str = ""
    summary: str = ""
    status: str = ""
    language: str = ""
    position: str = ""

    @property
    def lang_code(self) -> str:
        return LANGUAGE_WORD_TO_CODE.get(self.language.strip().lower(), "de")


@dataclass
class MediaRef:
    kind: MediaKind
    alt: str = ""
    source_path: Path | None = None
    filename: str = ""
    url: str = ""
    youtube_id: str = ""

    @property
    def exists(self) -> bool:
        return self.source_path is not None and self.source_path.exists()


@dataclass
class Block:
    kind: BlockKind
    raw: str
    heading_level: int = 0
    media: MediaRef | None = None


@dataclass
class Section:
    title: str | None = None
    media: list[MediaRef] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.title is None and not self.media and not self.texts


@dataclass
class BlogPost:
    meta: Meta
    blocks: list[Block]
    source_path: Path

    @property
    def slug(self) -> str:
        return f"{self.meta.date}_{self.meta.title.replace(' ', '_')}"

    @property
    def lang_code(self) -> str:
        return self.meta.lang_code

    @property
    def intro(self) -> str:
        if self.blocks and self.blocks[0].kind == "text":
            return self.blocks[0].raw
        return ""

    @property
    def header_media(self) -> MediaRef | None:
        if not self.meta.header:
            return None
        path = (self.source_path.parent / self.meta.header).resolve()
        ext = path.suffix.lower()
        kind: MediaKind = "video" if ext in VIDEO_EXTENSIONS else "image"
        return MediaRef(kind=kind, alt="featured", source_path=path, filename=path.name)

    @property
    def sections(self) -> list[Section]:
        sections: list[Section] = []
        current = Section()

        def flush() -> None:
            nonlocal current
            if not current.is_empty:
                sections.append(current)
            current = Section()

        content = self.blocks[1:] if (self.blocks and self.blocks[0].kind == "text") else self.blocks
        for block in content:
            if block.kind == "title":
                flush()
                current.title = block.raw.lstrip("#").strip()
            elif block.kind in ("media", "youtube"):
                if current.texts and current.title is None:
                    flush()
                if block.media is not None:
                    current.media.append(block.media)
            else:
                current.texts.append(block.raw)
        flush()
        return sections

    def all_media(self) -> list[MediaRef]:
        media = [b.media for b in self.blocks if b.media is not None]
        header = self.header_media
        return ([header] if header else []) + media

    def videos(self) -> list[MediaRef]:
        return [b.media for b in self.blocks if b.media is not None and b.media.kind == "video"]

    def section_for_block(self, media: MediaRef) -> Section | None:
        for section in self.sections:
            if media in section.media:
                return section
        return None

    def section_text_for_video(self, video: MediaRef, marker: str = REEL_VIDEO_MARKER) -> str:
        blocks = self.blocks
        start = 1 if (blocks and blocks[0].kind == "text") else 0
        idx = next((i for i in range(start, len(blocks)) if blocks[i].media is video), None)
        if idx is None:
            return marker
        lo = idx
        while lo - 1 >= start and blocks[lo - 1].kind == "text":
            lo -= 1
        hi = idx
        while hi + 1 < len(blocks) and blocks[hi + 1].kind == "text":
            hi += 1
        parts = [marker if i == idx else blocks[i].raw for i in range(lo, hi + 1)]
        return "\n\n".join(parts)


def _plain_inline(text: str) -> str:
    text = INLINE_IMAGE_RE.sub(r"\1", text)
    text = INLINE_LINK_RE.sub(r"\1", text)
    text = BOLD_RE.sub(r"\1", text)
    text = ITALIC_RE.sub(r"\1", text)
    return text.strip()


def _indent_tabs(line: str) -> int:
    return len(line) - len(line.lstrip("\t"))


def _consume_block(lines: list[str], start: int, level: int) -> tuple[list[str], int]:
    m = BULLET_RE.match(lines[start])
    assert m is not None and len(m.group(1)) == level
    out: list[str] = [m.group(2) or ""]
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        bm = BULLET_RE.match(line)
        if bm is not None:
            child_level = len(bm.group(1))
            if child_level <= level:
                break
            out.append(f"* {_plain_inline(bm.group(2) or '')}")
            i += 1
            continue
        tabs = _indent_tabs(line)
        rest = line[tabs:]
        if tabs == level and rest.startswith(" "):
            out.append(rest[2:] if rest.startswith("  ") else rest.lstrip(" "))
            i += 1
            continue
        if tabs > level:
            out.append(_plain_inline(rest))
            i += 1
            continue
        break
    return out, i


def _classify_block(out_lines: list[str], source_path: Path) -> Block:
    raw = "\n".join(out_lines).strip()
    first = out_lines[0].strip()

    hm = HEADING_RE.match(first)
    if hm is not None:
        return Block(kind="title", raw=raw, heading_level=len(hm.group(1)))

    ym = YOUTUBE_FIRST_LINE_RE.match(first)
    if ym is not None:
        url = ym.group(1)
        idm = YOUTUBE_ID_RE.search(url)
        media = MediaRef(kind="youtube", url=url, youtube_id=idm.group(1) if idm else "")
        return Block(kind="youtube", raw=raw, media=media)

    mm = MEDIA_FIRST_LINE_RE.match(first)
    if mm is not None:
        alt, rel_path = mm.group(1), mm.group(2)
        abs_path = (source_path.parent / rel_path).resolve()
        kind = "video" if abs_path.suffix.lower() in VIDEO_EXTENSIONS else "image"
        media = MediaRef(kind=kind, alt=alt, source_path=abs_path, filename=abs_path.name)
        return Block(kind="media", raw=raw, media=media)

    return Block(kind="text", raw=raw)


def _extract_path(raw: str) -> str:
    m = PAREN_PATH_RE.search(raw)
    return m.group(1) if m else raw


def _parse_meta(lines: list[str]) -> Meta:
    fields: dict[str, str] = {}
    for line in lines:
        m = PROP_RE.search(line)
        if m is None:
            continue
        fields[m.group(1)] = m.group(2).strip()
    return Meta(
        date=fields.get("date", ""),
        title=fields.get("title", ""),
        author=fields.get("author", ""),
        header=_extract_path(fields.get("header", "")) if fields.get("header") else "",
        summary=fields.get("summary", ""),
        status=fields.get("status", ""),
        language=fields.get("language", ""),
        position=fields.get("position", ""),
    )


def _extract_page_post(lines: list[str], source_path: Path) -> BlogPost | None:
    meta_lines = [line for line in lines if ROOT_PROP_RE.match(line)]
    if not any("type:: blog" in line for line in meta_lines):
        return None

    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        m = BULLET_RE.match(lines[i])
        if m is not None and len(m.group(1)) == 0:
            out, i = _consume_block(lines, i, 0)
            if "\n".join(out).strip():
                blocks.append(_classify_block(out, source_path))
        else:
            i += 1

    return BlogPost(meta=_parse_meta(meta_lines), blocks=blocks, source_path=source_path)


def extract_posts(source_path: Path) -> list[BlogPost]:
    text = source_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    page_post = _extract_page_post(lines, source_path)
    if page_post is not None:
        return [page_post]

    posts: list[BlogPost] = []
    i = 0
    while i < len(lines):
        m = BULLET_RE.match(lines[i])
        if m is None or "type:: blog" not in (m.group(2) or ""):
            i += 1
            continue

        level = len(m.group(1))
        meta_lines, i = _consume_block(lines, i, level)

        blocks: list[Block] = []
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            bm = BULLET_RE.match(line)
            if bm is None or len(bm.group(1)) < level:
                break
            if len(bm.group(1)) > level:
                i += 1
                continue
            out, i = _consume_block(lines, i, level)
            if "\n".join(out).strip():
                blocks.append(_classify_block(out, source_path))

        posts.append(BlogPost(meta=_parse_meta(meta_lines), blocks=blocks, source_path=source_path))

    return posts


def scan_blog_posts(journals_dir: Path, pages_dir: Path, online_only: bool = True) -> list[BlogPost]:
    posts: list[BlogPost] = []
    for directory in (journals_dir, pages_dir):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                posts.extend(extract_posts(path))
            except Exception:
                log.exception("failed to parse %s", path)

    if online_only:
        posts = [p for p in posts if p.meta.status == "online"]

    valid = []
    for post in posts:
        if not post.meta.date or not post.meta.title:
            log.warning("skipping post without date/title in %s", post.source_path)
            continue
        valid.append(post)

    valid.sort(key=lambda p: (p.meta.date, p.slug))
    return valid
