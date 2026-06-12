"""backlink node: link the blog post to its review page.

Inserts ``syndication:: [[syndicator/<slug>]]`` into the blog's property
block, idempotently. Both source formats are supported:

- journal format: the property block is a bullet (``- type:: blog``) with
  tab+two-space continuation lines; the property is appended as a new
  continuation line after the last existing one.
- page format: column-0 properties at the top of the file; the property is
  appended after the last leading property line.

Safe with respect to change detection: ``syndication`` is not a known meta
field, so the post's source hash (and therefore staleness) is unaffected.
"""

from __future__ import annotations

import logging
import os
import re

from ..model import BlogPost
from ..state import page_name

log = logging.getLogger(__name__)

BULLET_RE = re.compile(r"^(\t*)-(?: (.*))?$")
PROP_RE = re.compile(r"(\w+)::\s*(.*)")
ROOT_PROP_RE = re.compile(r"^\w+::\s*")

PROP_KEY = "syndication"


def _slug_of_prop_lines(prop_lines: list[str]) -> str:
    fields: dict[str, str] = {}
    for line in prop_lines:
        m = PROP_RE.search(line)
        if m is not None:
            fields[m.group(1)] = m.group(2).strip()
    return f"{fields.get('date', '')}_{fields.get('title', '').replace(' ', '_')}"


def _apply(lines: list[str], block: range, indent: str, target: str) -> bool:
    """Insert or update the syndication property inside the given line range.

    Returns True when the lines were modified.
    """
    for i in block:
        stripped = lines[i].lstrip("\t ")
        if stripped.startswith(f"{PROP_KEY}::"):
            existing_indent = lines[i][: len(lines[i]) - len(stripped)]
            replacement = f"{existing_indent}{PROP_KEY}:: {target}"
            if lines[i] == replacement:
                return False
            lines[i] = replacement
            return True
    lines.insert(block.stop, f"{indent}{PROP_KEY}:: {target}")
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
    while end < len(lines) and ROOT_PROP_RE.match(lines[end]):
        end += 1
    if end == 0:
        return None
    if not any("type:: blog" in line for line in lines[:end]):
        return None
    return range(0, end)


def ensure_syndication_link(post: BlogPost) -> bool:
    """Add/refresh the ``syndication::`` property; returns True when written."""
    target = f"[[{page_name(post.slug)}]]"
    path = post.source_path
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    page_block = _page_block_range(lines)
    if page_block is not None:
        changed = _apply(lines, page_block, "", target)
    else:
        found = _journal_block_range(lines, post.slug)
        if found is None:
            log.warning("no blog property block found in %s — skipping backlink", path)
            return False
        block, prefix = found
        changed = _apply(lines, block, prefix, target)

    if not changed:
        return False

    content = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    log.info("%s: added %s:: %s", path.name, PROP_KEY, target)
    return True
