"""extract node: parse Logseq markdown files into BlogPost objects.

Pure code, no LLM. Supports both source formats:

1. Journal format: a branch whose first block carries ``type:: blog`` as a
   bullet property block (typically nested under ``- [[Blog]]``).
2. Page format (e.g. pages/Renan.md): page properties at column 0 at the top
   of the file, content as top-level bullets.

The block *raw* text reproduces what the old Go converter emitted per block
(continuation lines dedented, nested bullets flattened to ``* ...`` with
inline markdown reduced to plain text), so the hugo node can reach output
parity by joining blocks with blank lines.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from ..model import VIDEO_EXTENSIONS, Block, BlogPost, MediaRef, Meta

log = logging.getLogger(__name__)

# A bullet is "- " plus content, or a bare "-" (empty bullet, occurs in practice).
BULLET_RE = re.compile(r"^(\t*)-(?: (.*))?$")
PROP_RE = re.compile(r"(\w+)::\s*(.*)")
ROOT_PROP_RE = re.compile(r"^\w+::\s*")  # page property at column 0
MEDIA_FIRST_LINE_RE = re.compile(r"^!\[(.*?)\]\(([^)]*?)\)(?:\{[^}]*\})?\s*$")
YOUTUBE_FIRST_LINE_RE = re.compile(r"^\{\{video\s+(https?://[^\s}]+)\s*\}\}\s*$")
YOUTUBE_ID_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]+)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
PAREN_PATH_RE = re.compile(r"\((.*?)\)")

INLINE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
INLINE_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")


def _plain_inline(text: str) -> str:
    """Reduce inline markdown to plain text, mimicking goldmark's ast.Text()."""
    text = INLINE_IMAGE_RE.sub(r"\1", text)
    text = INLINE_LINK_RE.sub(r"\1", text)
    text = BOLD_RE.sub(r"\1", text)
    text = ITALIC_RE.sub(r"\1", text)
    return text.strip()


def _indent_tabs(line: str) -> int:
    return len(line) - len(line.lstrip("\t"))


def _consume_block(lines: list[str], start: int, level: int) -> tuple[list[str], int]:
    """Consume one bullet block at the given tab level.

    Returns the block's output lines (bullet content, dedented continuation
    lines, flattened ``* ...`` child bullets) and the index of the next
    unconsumed line.
    """
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
            # Continuation line: tabs + (typically two) spaces.
            out.append(rest[2:] if rest.startswith("  ") else rest.lstrip(" "))
            i += 1
            continue
        if tabs > level:
            # Continuation of a nested child; append as plain text.
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
    """Mimic the old converter's extractPath(): value inside the first (...)."""
    m = PAREN_PATH_RE.search(raw)
    return m.group(1) if m else raw


def _parse_meta(lines: list[str]) -> Meta:
    fields: dict[str, str] = {}
    for line in lines:
        m = PROP_RE.search(line)
        if m is None:
            continue
        key, value = m.group(1), m.group(2).strip()
        fields[key] = value
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
    """Page format: column-0 properties at the top, content as top-level bullets."""
    meta_lines = [line for line in lines if ROOT_PROP_RE.match(line)]
    if not any("type:: blog" in line for line in meta_lines):
        return None

    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        m = BULLET_RE.match(lines[i])
        if m is not None and len(m.group(1)) == 0:
            out, i = _consume_block(lines, i, 0)
            raw = "\n".join(out).strip()
            if raw:
                blocks.append(_classify_block(out, source_path))
        else:
            i += 1

    return BlogPost(meta=_parse_meta(meta_lines), blocks=blocks, source_path=source_path)


def extract_posts(source_path: Path) -> list[BlogPost]:
    """Parse all blog posts (any status) from one Logseq markdown file."""
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
                # Defensive: stray deeper bullet without parent at our level.
                i += 1
                continue
            out, i = _consume_block(lines, i, level)
            raw = "\n".join(out).strip()
            if raw:
                blocks.append(_classify_block(out, source_path))

        posts.append(BlogPost(meta=_parse_meta(meta_lines), blocks=blocks, source_path=source_path))

    return posts


def source_hash(post: BlogPost) -> str:
    """Content hash of a post; stable across files and runs."""
    payload = {
        "meta": post.meta.model_dump(),
        "blocks": [b.raw for b in post.blocks],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return f"sha256:{digest}"


def scan_blog_posts(journals_dir: Path, pages_dir: Path, online_only: bool = True) -> list[BlogPost]:
    """Scan journals/ and pages/ for blog posts, sorted by date."""
    posts: list[BlogPost] = []
    for directory in (journals_dir, pages_dir):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                found = extract_posts(path)
            except Exception:
                log.exception("failed to parse %s", path)
                continue
            posts.extend(found)

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
