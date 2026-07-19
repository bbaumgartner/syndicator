"""The ``syndicated-at::`` marker on a blog's property block.

This is the *only* pipeline state in v2 (§2 / §6 of the migration design). It
means **handed off**, not published: the local client writes it once all
webhooks for a post returned ``{"status":"accepted"}``. An already-marked post
is skipped by the next ``syndicate`` (re-running would create duplicate drafts).

Both Logseq source formats are supported, idempotently:

- journal format: the property block is a bullet (``- type:: blog``) with
  tab+two-space continuation lines; the marker is appended as a new continuation
  line after the last existing property.
- page format: column-0 properties at the top of the file; the marker is
  appended after the last leading property line.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .model import BlogPost

log = logging.getLogger(__name__)

BULLET_RE = re.compile(r"^(\t*)-(?: (.*))?$")
PROP_RE = re.compile(r"(\w+)::\s*(.*)")
PAGE_PROP_RE = re.compile(r"^[\w-]+::\s*")

MARKER_KEY = "syndicated-at"


def now_iso() -> str:
    """Current time as a timezone-aware ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


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
    """Insert or update a property inside the given line range. Returns True when modified."""
    for i in block:
        stripped = lines[i].lstrip("\t ")
        if not stripped.startswith(f"{key}::"):
            continue
        existing_indent = lines[i][: len(lines[i]) - len(stripped)]
        replacement = f"{existing_indent}{key}:: {value}"
        if lines[i] == replacement:
            return False
        lines[i] = replacement
        return True
    lines.insert(block.stop, f"{indent}{key}:: {value}")
    return True


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


def _locate_blog_block(lines: list[str], post: BlogPost) -> tuple[range, str] | None:
    page_block = _page_block_range(lines)
    if page_block is not None:
        return page_block, ""
    return _journal_block_range(lines, post.slug)


def _write_source(path: Path, lines: list[str], trailing_newline: bool) -> None:
    content = "\n".join(lines) + ("\n" if trailing_newline else "")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def read_syndicated_at(post: BlogPost) -> str:
    """Read the ``syndicated-at::`` marker, or an empty string when absent."""
    lines = post.source_path.read_text(encoding="utf-8").splitlines()
    located = _locate_blog_block(lines, post)
    if located is None:
        return ""
    block, _ = located
    return _read_prop(lines, block, MARKER_KEY)


def is_syndicated(post: BlogPost) -> bool:
    return bool(read_syndicated_at(post))


def set_syndicated_at(post: BlogPost, value: str | None = None) -> bool:
    """Write the ``syndicated-at::`` marker (defaults to now). Returns True when written."""
    value = value or now_iso()
    path = post.source_path
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    located = _locate_blog_block(lines, post)
    if located is None:
        log.warning("no blog property block found in %s — cannot set %s", path, MARKER_KEY)
        return False
    block, prefix = located
    if not _set_prop(lines, block, prefix, MARKER_KEY, value):
        return False
    _write_source(path, lines, text.endswith("\n"))
    log.info("%s: set %s:: %s", path.name, MARKER_KEY, value)
    return True
