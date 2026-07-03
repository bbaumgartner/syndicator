"""Pipeline orchestration: wire nodes together, gate and record state.

The orchestrator composes the social pipeline as plain code here:
``plan -> caption -> media/package -> page write``. It loads the review
state, plans with the ``social_plan`` node, decides per block what is frozen
(published content is immutable) versus regenerated, captions with the
``caption`` node, and hands each intent to the ``export`` node — the
Logseq-edge writer that adapts media into a package and assembles the review
block. All conditional logic (channel selection, freezing) lives here, none
in the nodes.

The social pipeline runs independently of the site pipeline (translate ->
hugo -> journeymap -> git push). This module also composes the bootstrap and
parity pipelines as plain orchestrator code. All state lives on the per-post
review pages inside the Logseq graph (see state.py); the review itself happens
in Logseq.
"""

from __future__ import annotations

import logging
from datetime import date
from dataclasses import dataclass, field

from .config import Config
from .llm import LLMClient
from .model import BlogPost, PostIntent
from .nodes.backlink import ensure_syndication_link, read_hugo_hash, set_hugo_hash
from .nodes.caption import compose_post_text, generate_caption, youtube_links
from .nodes.export import build_post_block, cleanup_channel_assets, package_intent_media
from .nodes.extract import scan_blog_posts, source_hash
from .nodes.social_plan import plan_social
from .siteurl import resolve_post_url
from .state import PipelineLock, ReviewState, ReviewStore, SocialPostState, short_hash

_FROZEN_STATUSES = ("approved", "scheduled", "published")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParityCheck:
    slug: str
    index_name: str
    status: str  # "ok" | "diff" | "missing"


@dataclass
class BootstrapResult:
    posts: int = 0
    hugo_in_sync: list[str] = field(default_factory=list)
    hugo_stale: list[str] = field(default_factory=list)


def make_llm(cfg: Config) -> LLMClient:
    return LLMClient(max_retries=cfg.shared.translate.max_retries)


def make_store(cfg: Config) -> ReviewStore:
    return ReviewStore(cfg.pages_dir)


def scan_posts(cfg: Config) -> list[BlogPost]:
    return scan_blog_posts(cfg.journals_dir, cfg.pages_dir)


def find_post(cfg: Config, slug: str) -> BlogPost:
    posts = {p.slug: p for p in scan_posts(cfg)}
    if slug not in posts:
        known = "\n  ".join(sorted(posts))
        raise SystemExit(f"unknown post slug: {slug}\nknown posts:\n  {known}")
    return posts[slug]


def run_parity(cfg: Config) -> list[ParityCheck]:
    """Compare fresh source-language renders against the live site repo."""
    from .hugo_format import index_filename
    from .nodes.hugo import render_index

    ch = cfg.shared.channels["hugo"]
    checks: list[ParityCheck] = []
    for post in scan_posts(cfg):
        live = cfg.hugo_posts_dir / post.slug / index_filename(post.meta.language)
        if not live.exists():
            checks.append(ParityCheck(post.slug, live.name, "missing"))
            continue
        rendered = render_index(post, ch).encode("utf-8")
        status = "ok" if live.read_bytes() == rendered else "diff"
        checks.append(ParityCheck(post.slug, live.name, status))
    return checks


def run_bootstrap(cfg: Config) -> BootstrapResult:
    """Compose bootstrap: scan posts, compare live bundles, and write state."""
    from .nodes.bootstrap import hugo_bundle_hash

    store = make_store(cfg)
    posts = scan_posts(cfg)
    result = BootstrapResult(posts=len(posts))

    for post in posts:
        state = store.load(post.slug)
        hugo_hash = hugo_bundle_hash(cfg, post)
        store.save(state)
        set_hugo_hash(post, hugo_hash)
        ensure_syndication_link(post)
        if hugo_hash:
            result.hugo_in_sync.append(post.slug)
        else:
            result.hugo_stale.append(post.slug)

    return result


def mark_channels_published(cfg: Config, slug: str, channels: list[str] | None) -> list[str]:
    """Mark selected channel blocks on a review page as published."""
    store = make_store(cfg)
    if not store.exists(slug):
        raise ValueError(f"No review page for {slug} — run `syndicator catchup --post {slug}` first.")

    state = store.load(slug)
    social = list(cfg.social_channels())
    targets = channels or [c for c in social if state.channel_state(c) == "draft"]
    if not targets:
        raise ValueError("Nothing to mark: no draft channels and none given via --channel.")

    for channel in targets:
        if channel not in social:
            raise ValueError(f"Unknown channel: {channel}")
        posts = state.posts_for(channel)
        if not posts:
            raise ValueError(
                f"  {slug} {channel}: no blocks on review page — flip status:: in Logseq"
            )
        for post in posts:
            post.status = "published"

    store.save(state)
    return targets


def stale_draft_channels(cfg: Config, store: ReviewStore, post: BlogPost) -> list[str]:
    """Draft channels with blocks generated from an older source version."""
    state = store.load(post.slug)
    h = source_hash(post)
    return [
        name
        for name in cfg.social_channels()
        if state.channel_state(name) == "draft" and state.stale_posts(name, h)
    ]


def social_channels_to_export(cfg: Config, store: ReviewStore, post: BlogPost) -> list[str]:
    """Channels needing an export: pending ones plus stale drafts.

    Published channels (every block published) are immutable — the posts are
    live on the platform and cannot be changed, so they are never re-exported
    (not even with force). Individual published blocks inside a draft channel
    are frozen by the export node.
    """
    state = store.load(post.slug)
    pending = [name for name in cfg.social_channels() if state.channel_state(name) == "pending"]
    return pending + stale_draft_channels(cfg, store, post)


def next_catchup_post(cfg: Config, store: ReviewStore) -> BlogPost | None:
    """Oldest post that still has social channels to export."""
    for post in scan_posts(cfg):  # sorted by date
        if social_channels_to_export(cfg, store, post):
            return post
    return None


def _log_social_plan(slug: str, plans: dict[str, list[PostIntent]]) -> None:
    """Log the social_plan -> caption boundary: one line per planned intent."""
    for channel, intents in plans.items():
        for intent in intents:
            log.info(
                "plan %s %s #%d: %s/%s, %d media, %s",
                slug,
                channel,
                intent.index,
                intent.kind,
                intent.format,
                len(intent.media),
                intent.suggested_date or "-",
            )


def run_social_for_post(
    cfg: Config,
    post: BlogPost,
    llm: LLMClient | None = None,
    force: bool = False,
    verify_links: bool = True,
    start: date | None = None,
    channels: list[str] | None = None,
):
    """Generate social post blocks for one post on its review page.

    Default channel selection: pending plus stale drafts. ``force`` re-exports
    fresh drafts too. Published blocks are immutable and never regenerated.
    Returns the review page path, or None when there was nothing to do.
    """
    store = make_store(cfg)
    if channels is None:
        if force:
            state = store.load(post.slug)
            channels = [
                name for name in cfg.social_channels()
                if state.channel_state(name) != "published"
            ]
        else:
            channels = social_channels_to_export(cfg, store, post)
    if not channels:
        log.info("%s: no social channels to export (published is immutable)", post.slug)
        return None

    llm = llm or make_llm(cfg)
    state = store.load(post.slug)
    plans = plan_social(post, cfg, start)
    plans = {c: intents for c, intents in plans.items() if c in channels}
    _log_social_plan(post.slug, plans)
    src_hash = short_hash(source_hash(post))

    links: dict[str, str] = {}
    page = store.path_for(post.slug)
    for channel, intents in plans.items():
        lang = cfg.shared.channels[channel].language
        if lang not in links:
            links[lang] = resolve_post_url(cfg, post.slug, lang, verify=verify_links)
        url = links[lang]
        posts = _run_channel(cfg, post, llm, state, channel, intents, url, src_hash)
        cleanup_channel_assets(cfg, post.slug, channel, posts)
        state.replace_channel_posts(channel, posts)
        # Record state after each successful channel so a later channel's
        # failure never discards the finished (paid-for) LLM work above.
        page = store.save(state)

    log.info("review page written to %s", page)
    ensure_syndication_link(post)
    return page


def _run_channel(
    cfg: Config,
    post: BlogPost,
    llm: LLMClient,
    state: ReviewState,
    channel: str,
    intents: list[PostIntent],
    url: str,
    src_hash: str,
) -> list[SocialPostState]:
    """Freeze published blocks, (re)generate the rest for one channel.

    Frozen-vs-generate gating is orchestrator logic: blocks whose status is
    approved/scheduled/published are immutable and matched by position; every
    other slot is captioned, media-adapted and reassembled. Frozen blocks
    beyond the current plan length stay listed at the end.
    """
    ch_cfg = cfg.shared.channels[channel]
    frozen = {
        i: p
        for i, p in enumerate(state.posts_for(channel))
        if p.status in _FROZEN_STATUSES
    }
    posts: list[SocialPostState] = []
    for i, intent in enumerate(intents):
        if i in frozen:
            log.info("%s %s #%d: %s — frozen", post.slug, channel, i, frozen[i].status)
            posts.append(frozen.pop(i))
            continue
        log.info("caption %s #%d (%s)", channel, intent.index, intent.kind)
        draft = generate_caption(post, intent, cfg, llm)
        youtube = youtube_links(post, intent)
        text = compose_post_text(draft, intent, ch_cfg, url, youtube)
        media_rel = package_intent_media(cfg, post.slug, intent, llm)
        location = draft.location if channel in ("facebook", "instagram") else ""
        posts.append(
            build_post_block(intent, text, media_rel, youtube, location, src_hash)
        )
    posts.extend(frozen.values())
    return posts


# --- site pipeline ----------------------------------------------------------


def site_changed_posts(cfg: Config, store: ReviewStore) -> list[BlogPost]:
    """Posts whose content differs from what the hugo channel last processed."""
    changed = []
    for post in scan_posts(cfg):
        if read_hugo_hash(post) != short_hash(source_hash(post)):
            changed.append(post)
    return changed


def run_site_for_post(
    cfg: Config,
    post: BlogPost,
    llm: LLMClient,
    store: ReviewStore,
    try_run: bool = False,
    force: bool = False,
) -> bool:
    """Render the Hugo bundle and translations for one post.

    Returns True when the post was (re)generated. A try run does the real
    work (bundle + translations into the site repo working tree) but does
    not record the hugo state, so the next real run picks the post up again
    and commits (including re-translating).
    """
    from .nodes.hugo import bundle_media_plan, write_bundle
    from .nodes.media_adapt import adapt_or_copy
    from .nodes.translate import translate_bundle

    h = short_hash(source_hash(post))
    if not force and read_hugo_hash(post) == h:
        return False

    bundle = write_bundle(post, cfg.hugo_posts_dir, cfg)
    for src, dest_name in bundle_media_plan(post, cfg):
        adapt_or_copy(src, "hugo", cfg, bundle, llm, dest_name=dest_name)
    log.info("%s: hugo bundle written (%s)", post.slug, bundle)

    translated = translate_bundle(post, cfg, llm, bundle)
    if translated:
        log.info("%s: translated to %s", post.slug, ", ".join(translated))

    if not try_run:
        # Record the processed hash here (not only after push): an identical
        # re-render produces no git diff, and the post must not be retried
        # forever. A failed push raises and leaves the state untouched.
        state = store.load(post.slug)
        store.save(state)
        set_hugo_hash(post, h)
        ensure_syndication_link(post)
    return True


def run_all(
    cfg: Config,
    slugs: list[str] | None = None,
    try_run: bool = False,
    force: bool = False,
    site_only: bool = False,
    social_only: bool = False,
) -> None:
    """Full pipeline: site (hugo + translate + journeymap + git push) and the
    social exports for newly published posts.

    A try run does everything for real (LLM calls included) except the final
    git commit/push, so nothing goes live. Social blocks are exported too,
    without link verification: the slug-based post URLs only resolve once a
    real run pushes the site.
    """
    from .nodes.journeymap import generate_journey_map
    from .nodes.publish_git import commit_and_push, wait_for_deploy
    from .siteurl import post_url

    store = make_store(cfg)
    llm = make_llm(cfg)

    with PipelineLock(cfg.lock_path):
        if slugs:
            posts = [find_post(cfg, slug) for slug in slugs]
        else:
            posts = site_changed_posts(cfg, store) if not social_only else []

        new_posts: list[BlogPost] = []
        site_changed = False

        if not social_only:
            for post in posts:
                was_new = read_hugo_hash(post) == ""
                if run_site_for_post(cfg, post, llm, store, try_run=try_run, force=force):
                    site_changed = True
                    if was_new:
                        new_posts.append(post)

            if site_changed:
                generate_journey_map(cfg)
                if try_run:
                    log.info(
                        "try run: skipping commit/push — inspect with: git -C %s status",
                        cfg.local.sailingnomads_dir,
                    )
                else:
                    pushed = commit_and_push(cfg)
                    if pushed:
                        for post in new_posts:
                            set_hugo_hash(post, short_hash(source_hash(post)))
                            url = post_url(cfg, post.slug, cfg.shared.site.default_language)
                            wait_for_deploy(cfg, url)
            else:
                log.info("site: nothing changed")

        if not site_only:
            # In a try run the post is not live yet, so skip link
            # verification; the URLs resolve once a real run pushes.
            verify = not try_run
            if slugs:
                for post in [find_post(cfg, s) for s in slugs]:
                    run_social_for_post(cfg, post, llm=llm, force=force, verify_links=verify)
            else:
                new_slugs = {p.slug for p in new_posts}
                for post in new_posts:
                    run_social_for_post(cfg, post, llm=llm, force=force, verify_links=verify)
                # Edited posts: re-export only stale drafts. Pending channels
                # of older posts stay in the manual catch-up backlog.
                for post in scan_posts(cfg):
                    if post.slug in new_slugs:
                        continue
                    stale = stale_draft_channels(cfg, store, post)
                    if stale:
                        run_social_for_post(
                            cfg, post, llm=llm, verify_links=verify, channels=stale
                        )
