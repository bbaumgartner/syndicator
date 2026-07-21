"""Media adaptation into the SFTP staging layout (§4.1, Phase 2).

Adapts a post's media locally into a work directory laid out as the staging
area expects, and returns the list of (local, remote) uploads plus the
structured info the payload builders need.

Staging layout::

    <base>/sailingnomads/
        content/posts/<slug>/   Hugo index files + all bundle media
        static/journey-map.mp4
    <base>/<slug>/
        header/                 social header crops
        reels/<spec>/           reel videos
        covers/<spec>/          matching cover frames

Reels/covers are keyed by platform. Local adapts **once per distinct effective
spec** and reuses one uploaded file across platforms when the adapts coincide
(source short enough that trimming does not bite). ``spec_dir`` names the group:
plain aspect (``4x5``) when untrimmed, ``4x5-90s`` when trimmed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, VideoSpec
from .llm import LLMClient
from .model import BlogPost
from .nodes.hugo import bundle_media_plan
from .nodes.media_adapt import (
    adapt_media_for_channel,
    adapt_or_copy,
    extract_cover_frame,
    probe_video,
)
from . import payload as payload_mod

log = logging.getLogger(__name__)


@dataclass
class Upload:
    local: Path
    remote: str


@dataclass
class StagedVideo:
    index: int  # 1-based, over the post's content videos in document order
    alt: str
    section_title: str
    section_text: str
    reels: dict[str, str] = field(default_factory=dict)   # platform -> reel sftp_path
    covers: dict[str, str] = field(default_factory=dict)  # platform -> cover sftp_path


@dataclass
class StagedPost:
    slug: str
    uploads: list[Upload] = field(default_factory=list)
    header: dict[str, dict] = field(default_factory=dict)  # platform -> {"sftp_path": ...}
    videos: list[StagedVideo] = field(default_factory=list)


def _reel_group_dir(spec: VideoSpec, duration: float) -> str:
    aspect = (spec.aspect or "orig").replace(":", "x")
    trimmed = spec.max_seconds is not None and duration > spec.max_seconds
    return f"{aspect}-{spec.max_seconds}s" if trimmed else aspect


def _reel_signature(spec: VideoSpec, duration: float) -> tuple:
    """Two platforms share a reel file iff their effective adapts coincide."""
    trimmed = spec.max_seconds is not None and duration > spec.max_seconds
    effective_cap = spec.max_seconds if trimmed else None
    return (spec.aspect, spec.width, spec.height, spec.pad_mode, effective_cap)


def _plan_reel_groups(post: BlogPost, cfg: Config, duration: float) -> list[dict]:
    """Group social platforms by distinct effective reel adapt for one video."""
    groups: list[dict] = []
    by_sig: dict[tuple, dict] = {}
    used_dirs: set[str] = set()
    for name, ch in cfg.social_channels().items():
        spec = ch.reel_video
        if spec is None:
            continue
        sig = _reel_signature(spec, duration)
        if sig in by_sig:
            by_sig[sig]["platforms"].append(name)
            continue
        spec_dir = _reel_group_dir(spec, duration)
        if spec_dir in used_dirs:
            base = spec_dir
            i = 2
            while spec_dir in used_dirs:
                spec_dir = f"{base}-{i}"
                i += 1
        used_dirs.add(spec_dir)
        group = {"channel": name, "spec_dir": spec_dir, "platforms": [name]}
        by_sig[sig] = group
        groups.append(group)
    return groups


def stage_site(post: BlogPost, cfg: Config, llm: LLMClient, workdir: Path) -> list[Upload]:
    """Adapt the site bundle media (content assets + featured header)."""
    site_dir = workdir / "site"
    uploads: list[Upload] = []
    for src, dest in bundle_media_plan(post, cfg):
        out = adapt_or_copy(src, "hugo", cfg, site_dir, llm, dest_name=dest)
        uploads.append(Upload(out, payload_mod.site_remote(cfg, post.slug, dest)))
    return uploads


def stage_headers(
    post: BlogPost, cfg: Config, llm: LLMClient, workdir: Path
) -> tuple[list[Upload], dict[str, dict]]:
    """Adapt the per-platform social header crops."""
    header_media = post.header_media
    uploads: list[Upload] = []
    header: dict[str, dict] = {}
    if header_media is None or not header_media.exists:
        return uploads, header
    header_dir = workdir / "header"
    for platform in cfg.social_channels():
        out = adapt_media_for_channel(
            header_media, platform, cfg, header_dir, llm, dest_name=f"{platform}.jpg"
        )
        if out is None:
            log.warning("%s: header crop failed for %s", post.slug, platform)
            continue
        remote = payload_mod.header_remote(cfg, post.slug, platform)
        uploads.append(Upload(out, remote))
        header[platform] = {"sftp_path": remote}
    return uploads, header


def stage_reels(
    post: BlogPost, cfg: Config, llm: LLMClient, workdir: Path
) -> tuple[list[Upload], list[StagedVideo]]:
    """Adapt one reel (+ cover) per distinct effective spec, per content video."""
    uploads: list[Upload] = []
    staged: list[StagedVideo] = []
    for index, video in enumerate(post.videos(), start=1):
        if video.source_path is None or not video.source_path.exists():
            log.warning("%s: video %d missing on disk — skipping reel", post.slug, index)
            continue
        section = post.section_for_block(video)
        sv = StagedVideo(
            index=index,
            alt=video.alt or (video.source_path.name if video.source_path else ""),
            section_title=(section.title or "") if section else "",
            section_text=post.section_text_for_video(video),
        )
        try:
            duration = probe_video(video.source_path)["duration"]
        except Exception as err:  # noqa: BLE001 - fall back to no-trim grouping
            log.warning("%s: probe failed for video %d (%s)", post.slug, index, err)
            duration = 0.0

        for group in _plan_reel_groups(post, cfg, duration):
            reel_dir = workdir / "reels" / group["spec_dir"]
            reel_out = adapt_media_for_channel(
                video, group["channel"], cfg, reel_dir, llm,
                dest_name=f"{index}.mp4", post_format="reel",
            )
            if reel_out is None:
                log.warning("%s: reel adapt failed for video %d (%s)", post.slug, index, group["spec_dir"])
                continue
            cover_out = workdir / "covers" / group["spec_dir"] / f"{index}.jpg"
            extract_cover_frame(reel_out, cover_out)

            reel_remote = payload_mod.reel_remote(cfg, post.slug, group["spec_dir"], index)
            cover_remote = payload_mod.cover_remote(cfg, post.slug, group["spec_dir"], index)
            uploads.append(Upload(reel_out, reel_remote))
            uploads.append(Upload(cover_out, cover_remote))
            for platform in group["platforms"]:
                sv.reels[platform] = reel_remote
                sv.covers[platform] = cover_remote

        staged.append(sv)
    return uploads, staged


def stage_post(
    post: BlogPost,
    cfg: Config,
    llm: LLMClient,
    workdir: Path,
    *,
    include_social: bool,
) -> StagedPost:
    """Adapt everything for a post into ``workdir``.

    ``include_social`` (True for ``syndicate``, False for ``redeploy``) toggles
    header crops and reels; the site bundle is always staged.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    result = StagedPost(slug=post.slug)
    result.uploads.extend(stage_site(post, cfg, llm, workdir))
    if include_social:
        header_uploads, header = stage_headers(post, cfg, llm, workdir)
        result.uploads.extend(header_uploads)
        result.header = header
        reel_uploads, videos = stage_reels(post, cfg, llm, workdir)
        result.uploads.extend(reel_uploads)
        result.videos = videos
    return result
