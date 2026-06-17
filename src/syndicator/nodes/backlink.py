"""Blog property helpers: syndication link and hugo-hash on the post block.

Inserts pipeline-managed properties into the blog's ``type:: blog`` property
block, idempotently. Both source formats are supported:

- journal format: the property block is a bullet (``- type:: blog``) with
  tab+two-space continuation lines; properties are appended as new
  continuation lines after the last existing one.
- page format: column-0 properties at the top of the file; properties are
  appended after the last leading property line.

Safe with respect to change detection: ``syndication`` and ``hugo-hash`` are
not known meta fields, so the post's source hash (and therefore staleness) is
unaffected.
"""

from __future__ import annotations

import logging
import os
import re

from pathlib import Path

from ..model import BlogPost
from ..state import page_name

log = logging.getLogger(__name__)

BULLET_RE = re.compile(r"^(\t*)-(?: (.*))?$")
PROP_RE = re.compile(r"(\w+)::\s*(.*)")
PAGE_PROP_RE = re.compile(r"^[\w-]+::\s*")

SYNDICATION_KEY = "syndication"
HUGO_HASH_KEY = "hugo-hash"


def _slug_of_prop_lines(prop_lines: list[str]) -> str:
    fields: dict[str, str] = {}
    for line in prop_lines:
        m = PROP_RE.search(line)
        if m is not None:
            fields[m.group(1)] = m.group(2).strip()
    return f"{fields.get('date', '')}_{fields.get('title', '').replace(' ', '_')}"


def _read_prop(lines: list[str], block: range, key: str) -> str:
    for i in block:
        stripped = lines[i].lstrip("\t ")
        if stripped.startswith(f"{key}::"):
            return stripped.split("::", 1)[1].strip()
    return ""


def _set_prop(lines: list[str], block: range, indent: str, key: str, value: str) -> bool:
    """Insert, update, or remove a property inside the given line range.

    An empty *value* removes an existing line. Returns True when modified.
    """
    for i in block:
        stripped = lines[i].lstrip("\t ")
        if not stripped.startswith(f"{key}::"):
            continue
        if not value:
            lines.pop(i)
            return True
        existing_indent = lines[i][: len(lines[i]) - len(stripped)]
        replacement = f"{existing_indent}{key}:: {value}"
        if lines[i] == replacement:
            return False
        lines[i] = replacement
        return True
    if not value:
        return False
    lines.insert(block.stop, f"{indent}{key}:: {value}")
    return True


def _locate_blog_block(lines: list[str], post: BlogPost) -> tuple[range, str] | None:
    page_block = _page_block_range(lines)
    if page_block is not None:
        return page_block, ""
    found = _journal_block_range(lines, post.slug)
    if found is None:
        return None
    block, prefix = found
    return block, prefix


def _write_source(path: Path, lines: list[str], text: str) -> None:
    content = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def read_blog_prop(post: BlogPost, key: str) -> str:
    """Read a pipeline property from the blog's property block."""
    lines = post.source_path.read_text(encoding="utf-8").splitlines()
    located = _locate_blog_block(lines, post)
    if located is None:
        return ""
    block, _ = located
    return _read_prop(lines, block, key)


def set_blog_prop(post: BlogPost, key: str, value: str) -> bool:
    """Insert, update, or remove a pipeline property on the blog block."""
    path = post.source_path
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    located = _locate_blog_block(lines, post)
    if located is None:
        log.warning("no blog property block found in %s — skipping %s", path, key)
        return False
    block, prefix = located
    changed = _set_prop(lines, block, prefix, key, value)
    if not changed:
        return False
    _write_source(path, lines, text)
    log.info("%s: set %s:: %s", path.name, key, value or "(removed)")
    return True


def read_hugo_hash(post: BlogPost) -> str:
    return read_blog_prop(post, HUGO_HASH_KEY)


def set_hugo_hash(post: BlogPost, value: str) -> bool:
    return set_blog_prop(post, HUGO_HASH_KEY, value)


def _journal_block_range(lines: list[str], slug: str) -> tuple[range, str] | None:
    """Line range of the matching post's property block plus its line prefix."""
    i = 0
    while i < len(lines):
        m = BULLET_RE.match(lines[i])
        if m is None or "type:: blog" not in (m.group(2) or ""):
            i += 1
            continue
        level = len(m.group(1))
        start = i
        i += 1
        while i < len(lines):
            line = lines[i]
            if BULLET_RE.match(line) is not None:
                break
            if not line.startswith("\t" * level + " ") and line.strip():
                break
            i += 1
        if _slug_of_prop_lines(lines[start:i]) == slug:
            return range(start, i), "\t" * level + "  "
    return None


def _page_block_range(lines: list[str]) -> range | None:
    """Line range of leading column-0 page properties, if it is a blog page."""
    end = 0
    while end < len(lines) and PAGE_PROP_RE.match(lines[end]):
        end += 1
    if end == 0:
        return None
    if not any("type:: blog" in line for line in lines[:end]):
        return None
    return range(0, end)


def ensure_syndication_link(post: BlogPost) -> bool:
    """Add/refresh the ``syndication::`` property; returns True when written."""
    return set_blog_prop(post, SYNDICATION_KEY, f"[[{page_name(post.slug)}]]")
