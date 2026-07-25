"""Stage immutable originals into the SFTP ``source/`` layout.

The local client only copies originals into a work directory and returns the
(local, remote) uploads plus the video list for ``/reel`` webhooks. All
crop/resize/reencode happens in n8n; workflows must never modify ``source/``.

Staging layout (client uploads)::

    <base>/<slug>/source/
        header.<ext>            original header image
        <original basename>…    content images/videos (flat)

n8n later writes Hugo media under ``sailingnomads/content/posts/<slug>/`` and
social derivatives under ``<base>/<slug>/header|reels|covers/``.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .model import BlogPost
from .nodes.hugo import build_content, collect_asset_copies
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
    source_filename: str = ""


@dataclass
class StagedPost:
    slug: str
    uploads: list[Upload] = field(default_factory=list)
    header_source: str | None = None  # basename under source/, e.g. header.jpg
    videos: list[StagedVideo] = field(default_factory=list)


def _copy_into(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def stage_sources(post: BlogPost, cfg: Config, workdir: Path) -> StagedPost:
    """Copy every original asset for a post into ``workdir/source/``."""
    workdir.mkdir(parents=True, exist_ok=True)
    source_dir = workdir / "source"
    result = StagedPost(slug=post.slug)
    uploaded_names: set[str] = set()

    header_media = post.header_media
    if header_media is not None and header_media.exists and header_media.source_path is not None:
        ext = header_media.source_path.suffix
        name = f"header{ext}"
        local = _copy_into(header_media.source_path, source_dir / name)
        remote = payload_mod.source_remote(cfg, post.slug, name)
        result.uploads.append(Upload(local, remote))
        result.header_source = name
        uploaded_names.add(name)

    source_root = post.source_path.parent
    for src, basename in collect_asset_copies(build_content(post), source_root):
        if not src.exists():
            log.warning("missing asset %s", src)
            continue
        if basename in uploaded_names:
            continue
        local = _copy_into(src, source_dir / basename)
        remote = payload_mod.source_remote(cfg, post.slug, basename)
        result.uploads.append(Upload(local, remote))
        uploaded_names.add(basename)

    return result


def stage_video_manifest(post: BlogPost, cfg: Config) -> list[StagedVideo]:
    """Build ``/reel`` source entries (no encoding)."""
    staged: list[StagedVideo] = []
    for index, video in enumerate(post.videos(), start=1):
        if video.source_path is None or not video.source_path.exists():
            log.warning("%s: video %d missing on disk — skipping reel", post.slug, index)
            continue
        basename = video.source_path.name
        section = post.section_for_block(video)
        staged.append(
            StagedVideo(
                index=index,
                alt=video.alt or basename,
                section_title=(section.title or "") if section else "",
                section_text=post.section_text_for_video(video),
                source_filename=basename,
            )
        )
    return staged


def stage_post(
    post: BlogPost,
    cfg: Config,
    workdir: Path,
    *,
    include_social: bool,
) -> StagedPost:
    """Stage originals for a post into ``workdir``.

    ``include_social`` (True for ``syndicate``, False for ``redeploy``) toggles
    the video list used for ``/reel`` webhooks; originals are always uploaded.
    """
    result = stage_sources(post, cfg, workdir)
    if include_social:
        result.videos = stage_video_manifest(post, cfg)
    return result
