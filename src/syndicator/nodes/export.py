"""export node: the Logseq-review-package edge writer.

The orchestrator composes the social pipeline (plan -> caption ->
media/package -> page write); this node owns only the Logseq review-package
edge: how one planned post becomes a package directory of adapted media and
one block on the review page. It neither plans, captions, nor gates — the
orchestrator (pipeline.py) sequences those steps and decides which existing
blocks are frozen.

Output layout:

    <saillog>/pages/syndicator___<slug>.md          review page (state + captions)
    <saillog>/assets/syndicator/<slug>/<channel>/<nn>-<kind>/
        <media files>                               adapted for the channel

Each planned social post becomes one block on the review page: caption in a
code fence, adapted media embedded via ``../assets/...`` paths, status and
metadata as block properties. Published blocks are immutable: they are kept
verbatim (including their media directories) when a channel is regenerated —
the orchestrator selects which blocks survive; this node only writes.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from ..config import Config
from ..llm import LLMClient
from ..model import PostIntent
from ..state import PAGE_PREFIX, SocialPostState, caption_children
from .media_adapt import adapt_media_for_channel

log = logging.getLogger(__name__)

_EMBED_PATH_RE = re.compile(rf"\((\.\./assets/{PAGE_PREFIX}/[^)]+)\)")


def _package_dirname(intent: PostIntent) -> str:
    if intent.kind == "intro":
        return f"{intent.index:02d}-intro"
    title = (intent.section_title or "section").lower()
    title = re.sub(r"[^\w]+", "-", title, flags=re.UNICODE).strip("-") or "section"
    if intent.format == "reel" and intent.media:
        stem = Path(intent.media[0].filename).stem.lower()
        stem = re.sub(r"[^\w]+", "-", stem, flags=re.UNICODE).strip("-") or "reel"
        return f"{intent.index:02d}-{title}-reel-{stem}"
    if intent.format == "carousel":
        return f"{intent.index:02d}-{title}-carousel"
    return f"{intent.index:02d}-{title}"


def _post_title(intent: PostIntent) -> str:
    if intent.kind == "intro":
        return "Intro"
    base = intent.section_title or "Section"
    if intent.format == "reel":
        return f"{base} (Reel)"
    if intent.format == "carousel":
        return f"{base} (Carousel)"
    return base


def package_intent_media(
    cfg: Config,
    slug: str,
    intent: PostIntent,
    llm: LLMClient,
) -> list[str]:
    """Adapt one intent's media into its package dir (media_adapt step).

    Replaces the package dir wholesale — media adaptation recreates it — so a
    regenerated block never keeps stale files. Returns the ``../assets/...``
    embed paths, relative to the review page in ``pages/``.
    """
    dirname = _package_dirname(intent)
    pkg_dir = cfg.social_assets_dir / slug / intent.channel / dirname
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)

    media_rel: list[str] = []
    for media in intent.media:
        out = adapt_media_for_channel(
            media, intent.channel, cfg, pkg_dir, llm, post_format=intent.format
        )
        if out is not None:
            media_rel.append(
                f"../assets/{PAGE_PREFIX}/{slug}/{intent.channel}/{dirname}/{out.name}"
            )
    return media_rel


def build_post_block(
    intent: PostIntent,
    text: str,
    media_rel: list[str],
    youtube_links: list[str],
    location: str,
    source_hash: str,
) -> SocialPostState:
    """Assemble one review-page block (status: draft) from ready inputs."""
    return SocialPostState(
        channel=intent.channel,
        title=_post_title(intent),
        status="draft",
        publishing_date=intent.suggested_date,
        location=location,
        source_hash=source_hash,
        children=caption_children(text, media_rel, youtube_links),
    )


def _referenced_dirs(posts: list[SocialPostState]) -> set[str]:
    """Package dir names referenced by the media embeds of the given blocks.

    The package dir is the second-to-last path segment of every embed
    (``.../<channel>/<package-dir>/<file>``); counting segments from the left
    would break for slugs that contain ``/``.
    """
    dirs: set[str] = set()
    for post in posts:
        for line in post.children:
            for path in _EMBED_PATH_RE.findall(line):
                parts = path.split("/")
                if len(parts) >= 2:
                    dirs.add(parts[-2])
    return dirs


def _cleanup_channel_dir(channel_dir: Path, keep: set[str]) -> None:
    if not channel_dir.exists():
        return
    for sub in channel_dir.iterdir():
        if sub.is_dir() and sub.name not in keep:
            shutil.rmtree(sub)


def cleanup_channel_assets(
    cfg: Config, slug: str, channel: str, posts: list[SocialPostState]
) -> None:
    """Drop package dirs no longer referenced by the channel's blocks."""
    _cleanup_channel_dir(
        cfg.social_assets_dir / slug / channel, _referenced_dirs(posts)
    )
