"""Build the ``/publish`` and ``/reel`` webhook payloads (§4.2).

Pure functions: given a ``BlogPost`` (and the staged media info assembled by
``staging.py``) produce the exact JSON contracts. The local side ships
Hugo-ready content — ``raw`` for title/text blocks is already clean markdown with
Logseq asset references rewritten to bundle basenames — so the n8n Code node is a
thin emitter that does no Logseq parsing.
"""

from __future__ import annotations

from .config import Config
from .model import BlogPost
from .nodes.hugo import bundle_media_plan, summary_for, transform_content
from .nodes.media_adapt import output_basename
from .siteurl import post_url

JOURNEY_MAP_FILENAME = "journey-map.mp4"
JOURNEY_MAP_REPO_PATH = "static/journey-map.mp4"
SITE_STAGING_DIR = "sailingnomads"


def _base(cfg: Config) -> str:
    return cfg.shared.sftp.base_dir.rstrip("/")


def journey_map_remote(cfg: Config) -> str:
    return f"{_base(cfg)}/{SITE_STAGING_DIR}/{JOURNEY_MAP_REPO_PATH}"


def site_remote(cfg: Config, slug: str, name: str) -> str:
    return f"{_base(cfg)}/{SITE_STAGING_DIR}/content/posts/{slug}/{name}"


def header_remote(cfg: Config, slug: str, platform: str) -> str:
    return f"{_base(cfg)}/{slug}/header/{platform}.jpg"


def reel_remote(cfg: Config, slug: str, spec_dir: str, index: int) -> str:
    return f"{_base(cfg)}/{slug}/reels/{spec_dir}/{index}.mp4"


def cover_remote(cfg: Config, slug: str, spec_dir: str, index: int) -> str:
    return f"{_base(cfg)}/{slug}/covers/{spec_dir}/{index}.jpg"


def build_meta(post: BlogPost) -> dict:
    return {
        "title": post.meta.title,
        "date": post.meta.date,
        "language": post.meta.language,
        "lang_code": post.lang_code,
        "author": post.meta.author,
        "summary": summary_for(post),
        "position": post.meta.position,
    }


def build_blocks(post: BlogPost, cfg: Config) -> list[dict]:
    """Structured blocks for the n8n emitter (title/text/media/youtube)."""
    hugo = cfg.shared.channels["hugo"]
    slug = post.slug
    blocks: list[dict] = []
    for b in post.blocks:
        if b.kind == "title":
            blocks.append(
                {"kind": "title", "raw": transform_content(b.raw, hugo), "heading_level": b.heading_level}
            )
        elif b.kind == "text":
            blocks.append({"kind": "text", "raw": transform_content(b.raw, hugo)})
        elif b.kind == "youtube" or (b.media is not None and b.media.kind == "youtube"):
            yt = b.media.youtube_id if b.media else ""
            blocks.append({"kind": "youtube", "media": {"kind": "youtube", "youtube_id": yt}})
        elif b.kind == "media" and b.media is not None:
            m = b.media
            bundle_filename = output_basename(m.filename, hugo)
            blocks.append(
                {
                    "kind": "media",
                    "media": {
                        "kind": m.kind,
                        "bundle_filename": bundle_filename,
                        "sftp_path": site_remote(cfg, slug, bundle_filename),
                        "alt": m.alt,
                    },
                }
            )
    return blocks


def build_site_media(post: BlogPost, cfg: Config, *, include_journey_map: bool = True) -> list[dict]:
    """The authoritative media manifest for the site commit (§4.2)."""
    slug = post.slug
    out: list[dict] = []
    for _src, dest in bundle_media_plan(post, cfg):
        out.append(
            {
                "sftp_path": site_remote(cfg, slug, dest),
                "repo_path": f"content/posts/{slug}/{dest}",
                "bundle_filename": dest,
            }
        )
    if include_journey_map:
        out.append({"sftp_path": journey_map_remote(cfg), "repo_path": JOURNEY_MAP_REPO_PATH})
    return out


def build_publish_payload(
    post: BlogPost,
    cfg: Config,
    *,
    site_media: list[dict],
    header: dict[str, dict],
    redeploy: bool,
) -> dict:
    return {
        "slug": post.slug,
        "meta": build_meta(post),
        "post_url": post_url(cfg, post.slug, post.lang_code),
        "blocks": build_blocks(post, cfg),
        "site_media": site_media,
        "header": header,
        "flags": {"redeploy": redeploy},
    }


def build_reel_payload(
    post: BlogPost,
    cfg: Config,
    *,
    index: int,
    section_title: str,
    section_text: str,
    alt: str,
    reels: dict[str, str],
    covers: dict[str, str],
) -> dict:
    return {
        "slug": post.slug,
        "post": {
            "title": post.meta.title,
            "url": post_url(cfg, post.slug, post.lang_code),
            "summary": summary_for(post),
            "lang_code": post.lang_code,
        },
        "video": {
            "index": index,
            "section_title": section_title,
            "section_text": section_text,
            "alt": alt,
        },
        "files": {"reels": reels, "covers": covers},
    }
