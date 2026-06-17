"""Per-post state stored as generated Logseq review pages.

One page per blog post: ``pages/syndicator___<slug>.md`` (Logseq page name
``syndicator/<slug>``, the graph uses the triple-lowbar filename format).
The page lists every generated social media post with caption and media so
the review happens inside Logseq, and it carries *all* pipeline state as
Logseq properties:

- page properties (first bullet block): hugo status/hash, translation cache,
  explicit status for channels without generated blocks (substack, medium,
  bootstrap-published socials)
- block properties (one block per social post):
  ``status:: draft|approved|scheduled|published``, ``publishing-date::``,
  ``source-hash::``, ...

The user advances ``status::`` on each block in Logseq (draft → approved →
scheduled → published). Channels with blocks derive their status from the
blocks: all published -> published, otherwise draft.

Hashes on pages are shortened to 16 hex chars (pure equality tokens).
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .config import ALL_CHANNELS
from .model import BlogPost

log = logging.getLogger(__name__)

ChannelStatus = Literal["pending", "draft", "published"]
SocialPostStatus = Literal["draft", "approved", "scheduled", "published"]

_SOCIAL_POST_STATUSES = ("draft", "approved", "scheduled", "published")

PAGE_PREFIX = "syndicator"

BULLET_RE = re.compile(r"^(\t*)-(?: (.*))?$")
PROP_RE = re.compile(r"^([A-Za-z0-9_-]+)::\s*(.*)$")

# Characters Logseq percent-encodes in page file names (triple-lowbar format).
_INVALID_FILENAME_CHARS = '%/\\:*?"<>|'


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def short_hash(h: str) -> str:
    """Shorten a source hash to the equality token stored on review pages."""
    return h.removeprefix("sha256:")[:16]


def page_name(slug: str) -> str:
    return f"{PAGE_PREFIX}/{slug}"


def page_filename(slug: str) -> str:
    encoded = "".join(
        f"%{ord(c):02X}" if c in _INVALID_FILENAME_CHARS else c for c in slug
    )
    return f"{PAGE_PREFIX}___{encoded}.md"


def blog_page_ref(post: BlogPost) -> str:
    """Logseq reference to the page/journal that contains the blog post.

    The graph uses ``:journal/page-title-format "yyyy-MM-dd"``, so a journal
    page's name equals the post's ``date::`` property.
    """
    if post.source_path.parent.name == "journals":
        return f"[[{post.meta.date}]]"
    return f"[[{post.source_path.stem}]]"


# --- model ------------------------------------------------------------------


class SocialPostState(BaseModel):
    """One generated social media post == one block on the review page."""

    channel: str
    index: int = 0
    kind: str = "section"  # intro | section
    title: str = ""  # block text: section title or "Intro"
    status: SocialPostStatus = "draft"
    publishing_date: str = ""
    source_hash: str = ""  # short hash of the blog post at generation time
    generated_at: str = ""
    extra_props: list[str] = []  # unknown property lines (e.g. Logseq's id::)
    children: list[str] = []  # verbatim file lines: caption fence, media embeds


class ReviewState(BaseModel):
    """Everything the pipeline knows about one blog post, page-backed."""

    slug: str
    blog_ref: str = ""  # e.g. "[[2026-04-08]]" or "[[Renan]]"
    hugo_status: ChannelStatus = "pending"
    hugo_at: str = ""
    hugo_hash: str = ""  # short hash the hugo channel last processed
    translations: dict[str, str] = {}  # lang -> short hash of the source
    channel_status: dict[str, str] = {}  # explicit status for blockless channels
    extra_props: list[str] = []  # unknown page property lines, preserved
    posts: list[SocialPostState] = []

    @property
    def date(self) -> str:
        return self.slug.split("_", 1)[0]

    @property
    def title(self) -> str:
        parts = self.slug.split("_", 1)
        return parts[1].replace("_", " ") if len(parts) > 1 else self.slug

    def posts_for(self, channel: str) -> list[SocialPostState]:
        return sorted((p for p in self.posts if p.channel == channel), key=lambda p: p.index)

    def channel_state(self, channel: str) -> ChannelStatus:
        if channel == "hugo":
            return self.hugo_status
        posts = self.posts_for(channel)
        if posts:
            if all(p.status == "published" for p in posts):
                return "published"
            return "draft"
        explicit = self.channel_status.get(channel, "")
        if explicit in ("pending", "draft", "published"):
            return explicit  # type: ignore[return-value]
        return "pending"

    def stale_posts(self, channel: str, current_hash: str) -> list[SocialPostState]:
        """Draft posts generated from an older source version."""
        return [
            p
            for p in self.posts_for(channel)
            if p.status == "draft" and p.source_hash != short_hash(current_hash)
        ]

    def replace_channel_posts(self, channel: str, posts: list[SocialPostState]) -> None:
        self.posts = [p for p in self.posts if p.channel != channel] + posts
        self.channel_status.pop(channel, None)  # derived from blocks again


# --- page rendering ---------------------------------------------------------


def _channel_label(channel: str) -> str:
    return "X" if channel == "x" else channel.capitalize()


def _channel_order(posts: list[SocialPostState]) -> list[str]:
    seen = {p.channel for p in posts}
    ordered = [c for c in ALL_CHANNELS if c in seen]
    return ordered + sorted(seen - set(ordered))


def caption_children(caption: str, media_rel_paths: list[str], youtube_links: list[str]) -> list[str]:
    """Child block lines for a freshly generated social post block.

    Caption inside a code fence (exact copy, no accidental #tag links),
    one block per media embed, one ``{{video}}`` block per YouTube link.
    """
    lines = ["\t\t- ```"]
    for cap_line in caption.splitlines() or [""]:
        # Keep the continuation indent even on blank lines so the block
        # stays contiguous in Logseq's file format.
        lines.append(f"\t\t  {cap_line}")
    lines.append("\t\t  ```")
    for rel in media_rel_paths:
        lines.append(f"\t\t- ![{Path(rel).name}]({rel})")
    for url in youtube_links:
        lines.append(f"\t\t- {{{{video {url}}}}}")
    return lines


def _post_block_lines(post: SocialPostState) -> list[str]:
    title = post.title or ("Intro" if post.kind == "intro" else f"Post {post.index:02d}")
    lines = [f"\t- {title}"]
    props: list[tuple[str, str]] = [
        ("channel", post.channel),
        ("kind", post.kind),
        ("index", str(post.index)),
        ("status", post.status),
    ]
    if post.publishing_date:
        props.append(("publishing-date", post.publishing_date))
    if post.source_hash:
        props.append(("source-hash", post.source_hash))
    if post.generated_at:
        props.append(("generated-at", post.generated_at))
    lines.extend(f"\t  {key}:: {value}" for key, value in props)
    lines.extend(f"\t  {extra}" for extra in post.extra_props)
    lines.extend(post.children)
    return lines


def render_review_page(state: ReviewState) -> str:
    props: list[tuple[str, str]] = [
        ("type", PAGE_PREFIX),
        ("slug", state.slug),
        ("date", state.date),
    ]
    if state.blog_ref:
        props.append(("blog", state.blog_ref))
    props.append(("hugo-status", state.hugo_status))
    if state.hugo_at:
        props.append(("hugo-at", state.hugo_at))
    if state.hugo_hash:
        props.append(("hugo-hash", state.hugo_hash))
    for lang in sorted(state.translations):
        props.append((f"translation-{lang}", state.translations[lang]))
    for channel in sorted(state.channel_status):
        props.append((f"{channel}-status", state.channel_status[channel]))

    first = props[0]
    lines = [f"- {first[0]}:: {first[1]}"]
    lines.extend(f"  {key}:: {value}" for key, value in props[1:])
    lines.extend(f"  {extra}" for extra in state.extra_props)

    for channel in _channel_order(state.posts):
        lines.append(f"- {_channel_label(channel)}")
        for post in state.posts_for(channel):
            lines.extend(_post_block_lines(post))

    return "\n".join(lines) + "\n"


# --- page parsing -----------------------------------------------------------


class _RawBlock:
    """One bullet with its own continuation lines (children tracked via spans)."""

    def __init__(self, level: int, text: str, start: int):
        self.level = level
        self.text = text  # bullet line content
        self.start = start  # line index of the bullet line
        self.own_end = start + 1  # exclusive end of bullet + continuation lines
        self.prop_lines: list[str] = []  # continuation lines, stripped

    def props(self) -> dict[str, str]:
        fields: dict[str, str] = {}
        for line in [self.text, *self.prop_lines]:
            m = PROP_RE.match(line)
            if m is not None:
                fields[m.group(1).lower()] = m.group(2).strip()
        return fields


def _tokenize(lines: list[str]) -> list[_RawBlock]:
    blocks: list[_RawBlock] = []
    current: _RawBlock | None = None
    for idx, line in enumerate(lines):
        m = BULLET_RE.match(line)
        if m is not None:
            current = _RawBlock(len(m.group(1)), m.group(2) or "", idx)
            blocks.append(current)
        elif current is not None:
            if line.strip():
                current.prop_lines.append(line.strip())
            current.own_end = idx + 1
    return blocks


_PAGE_PROP_HANDLED = {"type", "slug", "date", "blog", "hugo-status", "hugo-at", "hugo-hash"}
_POST_PROP_HANDLED = {"channel", "kind", "index", "status", "publishing-date", "source-hash", "generated-at"}


def _coerce_status(value: str, allowed: tuple[str, ...], default: str) -> str:
    value = value.strip().lower()
    if value in allowed:
        return value
    if value:
        log.warning("unknown status %r on review page — treating as %r", value, default)
    return default


def _parse_page_props(block: _RawBlock, state: ReviewState) -> None:
    props = block.props()
    state.blog_ref = props.get("blog", "")
    state.hugo_status = _coerce_status(  # type: ignore[assignment]
        props.get("hugo-status", "pending"), ("pending", "draft", "published"), "pending"
    )
    state.hugo_at = props.get("hugo-at", "")
    state.hugo_hash = props.get("hugo-hash", "")
    for key, value in props.items():
        if key in _PAGE_PROP_HANDLED:
            continue
        if key.startswith("translation-"):
            state.translations[key.removeprefix("translation-")] = value
        elif key.endswith("-status"):
            state.channel_status[key.removesuffix("-status")] = _coerce_status(
                value, ("pending", "draft", "published"), "pending"
            )
        else:
            state.extra_props.append(f"{key}:: {value}")


def _parse_post_block(block: _RawBlock, children: list[str]) -> SocialPostState:
    props = block.props()
    try:
        index = int(props.get("index", "0"))
    except ValueError:
        index = 0
    extra = []
    for line in block.prop_lines:
        m = PROP_RE.match(line)
        if m is None or m.group(1).lower() not in _POST_PROP_HANDLED:
            extra.append(line)
    while children and not children[-1].strip():
        children.pop()
    return SocialPostState(
        channel=props["channel"],
        index=index,
        kind=props.get("kind", "section"),
        title=block.text,
        status=_coerce_status(props.get("status", "draft"), _SOCIAL_POST_STATUSES, "draft"),  # type: ignore[arg-type]
        publishing_date=props.get("publishing-date", ""),
        source_hash=props.get("source-hash", ""),
        generated_at=props.get("generated-at", ""),
        extra_props=extra,
        children=children,
    )


def parse_review_page(slug: str, text: str) -> ReviewState:
    lines = text.splitlines()
    state = ReviewState(slug=slug)
    page_props_seen = False

    # Legacy page-property format: bare ``key:: value`` lines before any bullet.
    leading = _RawBlock(0, "", 0)
    for line in lines:
        if BULLET_RE.match(line) is not None:
            break
        if line.strip():
            leading.prop_lines.append(line.strip())
    leading_props = leading.props()
    if leading_props.get("type") == PAGE_PREFIX or "slug" in leading_props:
        _parse_page_props(leading, state)
        page_props_seen = True

    blocks = _tokenize(lines)
    i = 0
    while i < len(blocks):
        block = blocks[i]
        props = block.props()
        if not page_props_seen and (props.get("type") == PAGE_PREFIX or "slug" in props):
            _parse_page_props(block, state)
            page_props_seen = True
            i += 1
        elif "channel" in props:
            # The post block's subtree (caption, media, user notes) is kept
            # verbatim so rewrites never mangle it.
            j = i + 1
            while j < len(blocks) and blocks[j].level > block.level:
                j += 1
            end = blocks[j].start if j < len(blocks) else len(lines)
            state.posts.append(_parse_post_block(block, lines[block.own_end:end]))
            i = j
        else:
            i += 1

    if not page_props_seen:
        log.warning("review page for %s has no property block — treating as fresh", slug)
    return state


# --- store ------------------------------------------------------------------


class ReviewStore:
    """Load/save review state from/to Logseq pages in the graph."""

    def __init__(self, pages_dir: Path):
        self.pages_dir = pages_dir

    def path_for(self, slug: str) -> Path:
        return self.pages_dir / page_filename(slug)

    def exists(self, slug: str) -> bool:
        return self.path_for(slug).exists()

    def load(self, slug: str) -> ReviewState:
        path = self.path_for(slug)
        if not path.exists():
            return ReviewState(slug=slug)
        return parse_review_page(slug, path.read_text(encoding="utf-8"))

    def save(self, state: ReviewState) -> Path:
        """Render and write the page; atomic, and only when content changed."""
        path = self.path_for(state.slug)
        content = render_review_page(state)
        if path.exists() and path.read_text(encoding="utf-8") == content:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
        return path

    def all(self) -> list[ReviewState]:
        if not self.pages_dir.exists():
            return []
        states = []
        for path in sorted(self.pages_dir.glob(f"{PAGE_PREFIX}___*.md")):
            text = path.read_text(encoding="utf-8")
            slug = _slug_from_page(path, text)
            states.append(parse_review_page(slug, text))
        return states


def _slug_from_page(path: Path, text: str) -> str:
    m = re.search(r"^\s*(?:- )?slug::\s*(.+)$", text, flags=re.MULTILINE)
    if m is not None:
        return m.group(1).strip()
    name = path.stem.removeprefix(f"{PAGE_PREFIX}___")
    return re.sub(r"%([0-9A-Fa-f]{2})", lambda mm: chr(int(mm.group(1), 16)), name)


# --- lock -------------------------------------------------------------------


class PipelineLock:
    """Simple cross-machine lock file with TTL inside the synced graph.

    Lives at ``<saillog>/.syndicator-lock.json`` (dotfile: ignored by the
    watcher, synced by Syncthing). Prevents the Mac and the server from
    processing simultaneously. Not a perfect distributed lock (Syncthing
    sync lag), but combined with idempotent state checks it is good enough
    for a two-machine setup.
    """

    def __init__(self, lock_path: Path, ttl_seconds: int = 3600):
        self.path = lock_path
        self.ttl = ttl_seconds

    def acquire(self) -> bool:
        if self.path.exists():
            try:
                info = json.loads(self.path.read_text(encoding="utf-8"))
                if time.time() - info.get("ts", 0) < self.ttl and info.get("host") != socket.gethostname():
                    return False
            except (json.JSONDecodeError, OSError):
                pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"host": socket.gethostname(), "pid": os.getpid(), "ts": time.time()}),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)
        return True

    def release(self) -> None:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
            if info.get("host") == socket.gethostname():
                self.path.unlink()
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def __enter__(self) -> "PipelineLock":
        if not self.acquire():
            raise RuntimeError(f"pipeline lock held by another machine: {self.path}")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
