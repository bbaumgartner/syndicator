"""social_plan node: turn a BlogPost into per-channel post intents.

Deterministic, no LLM. Per social channel: one intro post (intro text +
header image) plus one post per section, in order. Suggested posting dates
spread posts according to ``social.posts_per_week``. The user can still drop
or reorder posts during review — this node plans, it does not decide taste.

Channel-specific media rules:
- x:         one video OR up to ``max_media_per_post`` images (no mixing).
- instagram: needs at least one uploadable medium; falls back to the header
             image for text-only sections. YouTube links cannot be uploaded.
- facebook:  all section media up to the cap.
"""

from __future__ import annotations

from datetime import date, timedelta

from ..config import ChannelConfig, Config
from ..model import BlogPost, MediaRef, PostIntent, Section


def _uploadable(media: list[MediaRef]) -> list[MediaRef]:
    return [m for m in media if m.kind in ("image", "video") and m.exists]


def _select_media(channel: str, ch_cfg: ChannelConfig, media: list[MediaRef], header: MediaRef | None) -> list[MediaRef]:
    uploadable = _uploadable(media)
    cap = ch_cfg.max_media_per_post

    if channel == "x":
        videos = [m for m in uploadable if m.kind == "video"]
        if videos:
            return videos[:1]
        return [m for m in uploadable if m.kind == "image"][:cap]

    if channel == "instagram":
        selected = uploadable[:cap]
        if not selected and header is not None and header.exists:
            selected = [header]
        return selected

    return uploadable[:cap]


def _section_youtube(section: Section) -> list[str]:
    return [m.url for m in section.media if m.kind == "youtube" and m.url]


def plan_social(post: BlogPost, cfg: Config, start: date | None = None) -> dict[str, list[PostIntent]]:
    """Plan post intents for every enabled social channel."""
    start = start or date.today()
    spacing = 7.0 / max(cfg.shared.social.posts_per_week, 1)
    header = post.header_media

    plans: dict[str, list[PostIntent]] = {}
    for channel, ch_cfg in cfg.social_channels().items():
        intents: list[PostIntent] = []

        intro_media = _select_media(channel, ch_cfg, [header] if header else [], header)
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
            offset = timedelta(days=round((len(intents)) * spacing))
            intents.append(
                PostIntent(
                    channel=channel,
                    index=len(intents),
                    kind="section",
                    section_index=si,
                    section_title=section.title,
                    media=_select_media(channel, ch_cfg, section.media, header),
                    suggested_date=(start + offset).isoformat(),
                )
            )

        plans[channel] = intents
    return plans
