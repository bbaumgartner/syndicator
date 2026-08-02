"""Orchestration: stage originals, upload over SFTP, fire /reel + /publish."""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from .config import Config
from .extract import VIDEO_EXTENSIONS, BlogPost
from .marker import is_syndicated, read_syndicated_at, set_syndicated_at
from .sftp import SftpUploader, sftp_session
from .webhook import WebhookError, post_webhook

log = logging.getLogger(__name__)

ASSET_RE = re.compile(r"!\[(.*?)\]\((.*?assets/)(.*?)\)(?:\{[^}]*\})?")
LOGSEQ_VIDEO_RE = re.compile(r"\{\{video\s+(https?://[^\s}]+)\s*\}\}")
YOUTUBE_ID_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]+)")

_KEEP_CATEGORIES = ("L", "N", "M")
_KEEP_CHARS = set("-._")


# --- URL helpers -------------------------------------------------------------

def hugo_path_segment(name: str) -> str:
    out = []
    for ch in name:
        if ch in _KEEP_CHARS or unicodedata.category(ch)[0] in _KEEP_CATEGORIES:
            out.append(ch.lower())
    return "".join(out)


def lang_prefix(cfg: Config, lang: str) -> str:
    return "" if lang == cfg.shared.site.default_language else f"/{lang}"


def post_url(cfg: Config, slug: str, lang: str) -> str:
    segment = quote(hugo_path_segment(slug))
    return f"{cfg.shared.site.base_url}{lang_prefix(cfg, lang)}/posts/{segment}/"


# --- Content helpers ---------------------------------------------------------

def hugo_basename(original: str) -> str:
    """Mirror n8n Hugo naming (images keep name; videos → ``.mp4``)."""
    path = Path(original)
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        return f"{path.stem}.mp4"
    return path.name


def build_content(post: BlogPost) -> str:
    parts = [b.raw.strip() for b in post.blocks if b.raw.strip()]
    return "\n\n".join(parts)


def summary_for(post: BlogPost) -> str:
    if post.meta.summary:
        return post.meta.summary
    if post.blocks:
        return post.blocks[0].raw.replace("\n", " ")
    return ""


def collect_asset_copies(content: str, source_dir: Path) -> list[tuple[Path, str]]:
    copies: list[tuple[Path, str]] = []
    for m in ASSET_RE.finditer(content):
        src = (source_dir / (m.group(2) + m.group(3))).resolve()
        copies.append((src, Path(m.group(3)).name))
    return copies


def transform_content(content: str) -> str:
    def replace_video_embed(m: re.Match[str]) -> str:
        url = m.group(1)
        yt = YOUTUBE_ID_RE.search(url)
        if yt:
            return f"{{{{< youtube {yt.group(1)} >}}}}"
        return m.group(0)

    content = LOGSEQ_VIDEO_RE.sub(replace_video_embed, content)

    def replace_asset(m: re.Match[str]) -> str:
        alt = m.group(1)
        filename = Path(m.group(3)).name
        if Path(filename).suffix.lower() in VIDEO_EXTENSIONS:
            return f'{{{{< video src="{filename}" >}}}}'
        return f"![{alt}]({filename})"

    return ASSET_RE.sub(replace_asset, content)


# --- SFTP path helpers -------------------------------------------------------

def _base(cfg: Config) -> str:
    return cfg.shared.sftp.base_dir.rstrip("/")


def source_remote(cfg: Config, slug: str, name: str) -> str:
    return f"{_base(cfg)}/{slug}/source/{name}"


# --- Payload builders --------------------------------------------------------

def build_blocks(post: BlogPost) -> list[dict]:
    blocks: list[dict] = []
    for b in post.blocks:
        if b.kind == "title":
            blocks.append(
                {"kind": "title", "raw": transform_content(b.raw), "heading_level": b.heading_level}
            )
        elif b.kind == "text":
            blocks.append({"kind": "text", "raw": transform_content(b.raw)})
        elif b.kind == "youtube" or (b.media is not None and b.media.kind == "youtube"):
            yt = b.media.youtube_id if b.media else ""
            blocks.append({"kind": "youtube", "media": {"kind": "youtube", "youtube_id": yt}})
        elif b.kind == "media" and b.media is not None:
            m = b.media
            blocks.append(
                {
                    "kind": "media",
                    "media": {
                        "kind": m.kind,
                        "source_filename": Path(m.filename).name,
                        "alt": m.alt,
                    },
                }
            )
    return blocks


# --- Staging -----------------------------------------------------------------

@dataclass
class Upload:
    local: Path
    remote: str


@dataclass
class StagedVideo:
    index: int
    alt: str
    section_title: str
    section_text: str
    source_filename: str = ""


@dataclass
class StagedPost:
    slug: str
    uploads: list[Upload] = field(default_factory=list)
    header_source: str | None = None
    videos: list[StagedVideo] = field(default_factory=list)


def _copy_into(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def stage_post(
    post: BlogPost,
    cfg: Config,
    workdir: Path,
    *,
    include_social: bool,
) -> StagedPost:
    workdir.mkdir(parents=True, exist_ok=True)
    source_dir = workdir / "source"
    result = StagedPost(slug=post.slug)
    uploaded_names: set[str] = set()

    header_media = post.header_media
    if header_media is not None and header_media.exists and header_media.source_path is not None:
        ext = header_media.source_path.suffix
        name = f"header{ext}"
        local = _copy_into(header_media.source_path, source_dir / name)
        result.uploads.append(Upload(local, source_remote(cfg, post.slug, name)))
        result.header_source = name
        uploaded_names.add(name)

    for src, basename in collect_asset_copies(build_content(post), post.source_path.parent):
        if not src.exists():
            log.warning("missing asset %s", src)
            continue
        if basename in uploaded_names:
            continue
        local = _copy_into(src, source_dir / basename)
        result.uploads.append(Upload(local, source_remote(cfg, post.slug, basename)))
        uploaded_names.add(basename)

    if include_social:
        for index, video in enumerate(post.videos(), start=1):
            if video.source_path is None or not video.source_path.exists():
                log.warning("%s: video %d missing on disk — skipping reel", post.slug, index)
                continue
            basename = video.source_path.name
            section = post.section_for_block(video)
            result.videos.append(
                StagedVideo(
                    index=index,
                    alt=video.alt or basename,
                    section_title=(section.title or "") if section else "",
                    section_text=post.section_text_for_video(video),
                    source_filename=basename,
                )
            )
    return result


# --- Pipeline ----------------------------------------------------------------

@dataclass
class SyndicateReport:
    done: list[str] = field(default_factory=list)
    skipped_marked: list[str] = field(default_factory=list)
    skipped_no_header: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def _scan_online(cfg: Config) -> list[BlogPost]:
    from .extract import scan_blog_posts

    return scan_blog_posts(cfg.journals_dir, cfg.pages_dir, online_only=True)


def list_syndication(cfg: Config) -> list[tuple[str, str]]:
    """Return (slug, syndicated-at) for every online post; date empty if not marked."""
    return [(p.slug, read_syndicated_at(p)) for p in _scan_online(cfg)]


def _has_header(post: BlogPost) -> bool:
    hm = post.header_media
    return hm is not None and hm.exists


def _upload_all(sftp: SftpUploader, staged: StagedPost) -> None:
    for up in staged.uploads:
        sftp.upload(up.local, up.remote)


def _check_webhooks(cfg: Config, *, need_reel: bool) -> None:
    if not cfg.shared.webhooks.publish_url:
        raise SystemExit("webhooks.publish_url is not configured in syndicator.yaml")
    if need_reel and not cfg.shared.webhooks.reel_url:
        raise SystemExit("webhooks.reel_url is not configured in syndicator.yaml")


def syndicate(cfg: Config, slug: str | None = None) -> SyndicateReport:
    _check_webhooks(cfg, need_reel=True)
    posts = _scan_online(cfg)
    if slug is not None:
        posts = [p for p in posts if p.slug == slug]
        if not posts:
            raise SystemExit(f"unknown online post slug: {slug}")

    report = SyndicateReport()
    with tempfile.TemporaryDirectory(prefix="syndicator-") as tmp_root:
        tmp = Path(tmp_root)
        with sftp_session(cfg) as sftp:
            for post in posts:
                _syndicate_one(cfg, sftp, post, tmp, report)

    log.info(
        "syndicate: %d done, %d already-marked, %d no-header, %d failed",
        len(report.done),
        len(report.skipped_marked),
        len(report.skipped_no_header),
        len(report.failed),
    )
    for s, reason in report.failed:
        log.error("  failed: %s (%s)", s, reason)
    for s in report.skipped_no_header:
        log.warning("  skipped (no header): %s", s)
    return report


def _syndicate_one(
    cfg: Config,
    sftp: SftpUploader,
    post: BlogPost,
    tmp: Path,
    report: SyndicateReport,
) -> None:
    slug = post.slug
    if is_syndicated(post):
        log.info("skip %s: already syndicated (delete the marker to re-run)", slug)
        report.skipped_marked.append(slug)
        return
    if not _has_header(post):
        log.warning("skip %s: no header:: image (required — add one and re-run)", slug)
        report.skipped_no_header.append(slug)
        return

    try:
        staged = stage_post(post, cfg, tmp / slug, include_social=True)
        _upload_all(sftp, staged)

        for sv in staged.videos:
            if not sv.source_filename:
                log.warning("%s: video %d has no source filename — skipping /reel", slug, sv.index)
                continue
            post_webhook(
                cfg.shared.webhooks.reel_url,
                {
                    "slug": post.slug,
                    "post": {
                        "title": post.meta.title,
                        "url": post_url(cfg, post.slug, post.lang_code),
                        "summary": summary_for(post),
                        "lang_code": post.lang_code,
                    },
                    "video": {
                        "index": sv.index,
                        "section_title": sv.section_title,
                        "section_text": sv.section_text,
                        "alt": sv.alt,
                    },
                    "source": {"filename": sv.source_filename},
                },
                label=f"/reel {slug} #{sv.index}",
            )

        post_webhook(
            cfg.shared.webhooks.publish_url,
            {
                "slug": post.slug,
                "meta": {
                    "title": post.meta.title,
                    "date": post.meta.date,
                    "language": post.meta.language,
                    "lang_code": post.lang_code,
                    "author": post.meta.author,
                    "summary": summary_for(post),
                    "position": post.meta.position,
                },
                "post_url": post_url(cfg, post.slug, post.lang_code),
                "blocks": build_blocks(post),
                "header_source": staged.header_source or "",
                "flags": {"redeploy": False},
            },
            label=f"/publish {slug}",
        )

        set_syndicated_at(post)
        report.done.append(slug)
    except (WebhookError, OSError) as err:
        log.error("%s: syndication failed — %s (marker not set; will retry next run)", slug, err)
        report.failed.append((slug, str(err)))


def redeploy(cfg: Config, slug: str) -> None:
    _check_webhooks(cfg, need_reel=False)
    posts = {p.slug: p for p in _scan_online(cfg)}
    if slug not in posts:
        known = "\n  ".join(sorted(posts))
        raise SystemExit(f"unknown online post slug: {slug}\nknown posts:\n  {known}")
    post = posts[slug]
    if not _has_header(post):
        raise SystemExit(f"{slug}: no header:: image — the site build requires a featured image")

    with tempfile.TemporaryDirectory(prefix="syndicator-") as tmp_root:
        tmp = Path(tmp_root)
        staged = stage_post(post, cfg, tmp / slug, include_social=False)

        with sftp_session(cfg) as sftp:
            _upload_all(sftp, staged)
            post_webhook(
                cfg.shared.webhooks.publish_url,
                {
                    "slug": post.slug,
                    "meta": {
                        "title": post.meta.title,
                        "date": post.meta.date,
                        "language": post.meta.language,
                        "lang_code": post.lang_code,
                        "author": post.meta.author,
                        "summary": summary_for(post),
                        "position": post.meta.position,
                    },
                    "post_url": post_url(cfg, post.slug, post.lang_code),
                    "blocks": build_blocks(post),
                    "header_source": staged.header_source or "",
                    "flags": {"redeploy": True},
                },
                label=f"/publish redeploy {slug}",
            )

    log.info("redeploy %s: site rebuild handed off (no social, no marker)", slug)
