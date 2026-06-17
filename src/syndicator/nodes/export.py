"""export node: generate social post blocks on the Logseq review page.

Output layout:

    <saillog>/pages/syndicator___<slug>.md          review page (state + captions)
    <saillog>/assets/syndicator/<slug>/<channel>/<nn>-<kind>/
        <media files>                               adapted for the channel

Each planned social post becomes one block on the review page: caption in a
code fence, adapted media embedded via ``../assets/...`` paths, status and
metadata as block properties. Published blocks are immutable: they are kept
verbatim (including their media directories) when a channel is regenerated.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import date
from pathlib import Path

from ..config import Config
from ..llm import LLMClient
from ..model import BlogPost, PostIntent, SocialDraft
from ..siteurl import resolve_post_url
from ..state import (
    PAGE_PREFIX,
    ReviewStore,
    SocialPostState,
    caption_children,
    short_hash,
)
from .caption import _youtube_links, compose_post_text, generate_caption
from .extract import source_hash
from .media_adapt import adapt_media_for_channel
from .social_plan import plan_social

log = logging.getLogger(__name__)

_ASSET_DIR_RE = re.compile(rf"\.\./assets/{PAGE_PREFIX}/[^/)]+/[^/)]+/([^/)]+)/")


def _package_dirname(intent: PostIntent) -> str:
    if intent.kind == "intro":
        return f"{intent.index:02d}-intro"
    title = (intent.section_title or "section").lower()
    title = re.sub(r"[^\w]+", "-", title, flags=re.UNICODE).strip("-") or "section"
    return f"{intent.index:02d}-{title}"


def _referenced_dirs(posts: list[SocialPostState]) -> set[str]:
    """Package dir names referenced by the media embeds of the given blocks."""
    dirs: set[str] = set()
    for post in posts:
        for line in post.children:
            dirs.update(_ASSET_DIR_RE.findall(line))
    return dirs


def _cleanup_channel_dir(channel_dir: Path, keep: set[str]) -> None:
    if not channel_dir.exists():
        return
    for sub in channel_dir.iterdir():
        if sub.is_dir() and sub.name not in keep:
            shutil.rmtree(sub)


def generate_post_block(
    post: BlogPost,
    intent: PostIntent,
    draft: SocialDraft,
    url: str,
    cfg: Config,
    llm: LLMClient,
) -> SocialPostState:
    """Adapt media and assemble one social post block (status: draft)."""
    ch_cfg = cfg.shared.channels[intent.channel]
    dirname = _package_dirname(intent)
    pkg_dir = cfg.social_assets_dir / post.slug / intent.channel / dirname
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)  # replace wholesale; media adaptation recreates it

    media_rel: list[str] = []
    for media in intent.media:
        out = adapt_media_for_channel(media, intent.channel, cfg, pkg_dir, llm)
        if out is not None:
            media_rel.append(
                f"../assets/{PAGE_PREFIX}/{post.slug}/{intent.channel}/{dirname}/{out.name}"
            )

    youtube = _youtube_links(post, intent)
    text = compose_post_text(draft, intent, ch_cfg, url, youtube)

    return SocialPostState(
        channel=intent.channel,
        title=intent.section_title or ("Intro" if intent.kind == "intro" else ""),
        status="draft",
        publishing_date=intent.suggested_date,
        source_hash=short_hash(source_hash(post)),
        children=caption_children(text, media_rel, youtube),
    )


def export_social(
    post: BlogPost,
    cfg: Config,
    llm: LLMClient,
    channels: list[str] | None = None,
    verify_links: bool = True,
    start: date | None = None,
) -> Path:
    """Run the social pipeline for one post: plan, caption, adapt, write page.

    Returns the path of the review page. Existing published blocks are kept
    untouched; everything else in the selected channels is regenerated.
    """
    store = ReviewStore(cfg.pages_dir)
    state = store.load(post.slug)

    plans = plan_social(post, cfg, start)
    if channels is not None:
        plans = {c: intents for c, intents in plans.items() if c in channels}

    links: dict[str, str] = {}
    for channel, intents in plans.items():
        lang = cfg.shared.channels[channel].language
        if lang not in links:
            links[lang] = resolve_post_url(cfg, post.slug, lang, verify=verify_links)
        url = links[lang]

        frozen = {
            i: p
            for i, p in enumerate(state.posts_for(channel))
            if p.status in ("approved", "scheduled", "published")
        }
        new_posts: list[SocialPostState] = []
        for i, intent in enumerate(intents):
            if i in frozen:
                log.info("%s %s #%d: %s — frozen", post.slug, channel, i, frozen[i].status)
                new_posts.append(frozen.pop(i))
                continue
            log.info("caption %s #%d (%s)", channel, intent.index, intent.kind)
            draft = generate_caption(post, intent, cfg, llm)
            new_posts.append(generate_post_block(post, intent, draft, url, cfg, llm))
        # Frozen blocks beyond the current plan length stay listed.
        new_posts.extend(frozen.values())

        _cleanup_channel_dir(
            cfg.social_assets_dir / post.slug / channel, _referenced_dirs(new_posts)
        )
        state.replace_channel_posts(channel, new_posts)

    page = store.save(state)
    log.info("review page written to %s", page)
    return page
