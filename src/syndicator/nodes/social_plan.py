"""social_plan node: turn a BlogPost into per-channel post intents.

Deterministic, no LLM. Per social channel: one intro post (intro text +
header image) plus planned posts per section, in order. Suggested posting
dates spread posts according to ``social.posts_per_week``. The user can
still drop or reorder posts during review — this node plans, it does not
decide taste.

Section media rules, driven by channel configuration (see ``ChannelConfig``
in ``config.py``):
- Channels with a ``reel_video`` spec (e.g. Instagram, Facebook): sections
  with uploadable videos get one reel post per video, plus one carousel with
  all uploadable media when images are present too. Sections without videos
  get one single post.
- ``video_exclusive`` channels (e.g. X) keep one post per section: one video
  OR up to ``max_media_per_post`` images (no mixing; video wins when
  present) — sections are never split into reel/carousel posts.
- ``fallback_to_header`` channels (e.g. Instagram) fall back to the post's
  header image when a part has no other usable media.
"""

from __future__ import annotations

from datetime import date, timedelta

from ..config import ChannelConfig, Config
from ..model import BlogPost, MediaRef, PostIntent, Section


def _uploadable(media: list[MediaRef]) -> list[MediaRef]:
    return [m for m in media if m.kind in ("image", "video") and m.exists]


def _select_single_media(
    ch_cfg: ChannelConfig, media: list[MediaRef], header: MediaRef | None
) -> list[MediaRef]:
    uploadable = _uploadable(media)
    cap = ch_cfg.max_media_per_post

    if ch_cfg.video_exclusive:
        videos = [m for m in uploadable if m.kind == "video"]
        if videos:
            return videos[:1]
        return [m for m in uploadable if m.kind == "image"][:cap]

    selected = uploadable[:cap]
    if ch_cfg.fallback_to_header and not selected and header is not None and header.exists:
        selected = [header]
    return selected


def _select_carousel_media(ch_cfg: ChannelConfig, media: list[MediaRef]) -> list[MediaRef]:
    uploadable = _uploadable(media)
    cap = ch_cfg.max_media_per_post

    if ch_cfg.video_exclusive:
        return [m for m in uploadable if m.kind == "image"][:cap]

    return uploadable[:cap]


def _plan_section_intents(
    channel: str,
    ch_cfg: ChannelConfig,
    section: Section,
    section_index: int,
    header: MediaRef | None,
) -> list[PostIntent]:
    if ch_cfg.reel_video is None:
        return [
            PostIntent(
                channel=channel,
                index=0,
                kind="section",
                format="single",
                section_index=section_index,
                section_title=section.title,
                media=_select_single_media(ch_cfg, section.media, header),
            )
        ]

    uploadable = _uploadable(section.media)
    videos = [m for m in uploadable if m.kind == "video"]
    images = [m for m in uploadable if m.kind == "image"]

    if not videos:
        return [
            PostIntent(
                channel=channel,
                index=0,  # filled in by caller
                kind="section",
                format="single",
                section_index=section_index,
                section_title=section.title,
                media=_select_single_media(ch_cfg, section.media, header),
            )
        ]

    intents: list[PostIntent] = []
    for video in videos:
        intents.append(
            PostIntent(
                channel=channel,
                index=0,
                kind="section",
                format="reel",
                section_index=section_index,
                section_title=section.title,
                media=[video],
            )
        )

    if images:
        intents.append(
            PostIntent(
                channel=channel,
                index=0,
                kind="section",
                format="carousel",
                section_index=section_index,
                section_title=section.title,
                media=_select_carousel_media(ch_cfg, section.media),
            )
        )

    return intents


def plan_social(post: BlogPost, cfg: Config, start: date | None = None) -> dict[str, list[PostIntent]]:
    """Plan post intents for every enabled social channel."""
    start = start or date.today()
    spacing = 7.0 / max(cfg.shared.social.posts_per_week, 1)
    header = post.header_media

    plans: dict[str, list[PostIntent]] = {}
    for channel, ch_cfg in cfg.social_channels().items():
        intents: list[PostIntent] = []

        intro_media = _select_single_media(ch_cfg, [header] if header else [], header)
        intents.append(
            PostIntent(
                channel=channel,
                index=0,
                kind="intro",
                media=intro_media,
                suggested_date=start.isoformat(),
            )
        )

        for si, section in enumerate(post.sections):
            for intent in _plan_section_intents(channel, ch_cfg, section, si, header):
                offset = timedelta(days=round(len(intents) * spacing))
                intent.index = len(intents)
                intent.suggested_date = (start + offset).isoformat()
                intents.append(intent)

        plans[channel] = intents
    return plans
