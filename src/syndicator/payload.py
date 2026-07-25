"""Build the ``/publish`` and ``/reel`` webhook payloads (§4.2).

Pure functions: given a ``BlogPost`` produce the JSON contracts. The local side
ships Hugo-ready block text and points at immutable ``source/`` originals by
basename; n8n derives SFTP paths from ``slug`` + layout conventions and adapts
media into the sailingnomads post tree and social derivatives.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .model import BlogPost
from .nodes.hugo import summary_for, transform_content
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


def source_remote(cfg: Config, slug: str, name: str) -> str:
    return f"{_base(cfg)}/{slug}/source/{name}"


def header_remote(cfg: Config, slug: str, platform: str) -> str:
    return f"{_base(cfg)}/{slug}/header/{platform}.jpg"


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


def build_blocks(post: BlogPost) -> list[dict]:
    """Structured blocks for the n8n emitter (title/text/media/youtube)."""
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


def build_publish_payload(
    post: BlogPost,
    cfg: Config,
    *,
    header_source: str | None,
    redeploy: bool,
) -> dict:
    return {
        "slug": post.slug,
        "meta": build_meta(post),
        "post_url": post_url(cfg, post.slug, post.lang_code),
        "blocks": build_blocks(post),
        "header_source": header_source or "",
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
    source_filename: str,
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
        "source": {"filename": source_filename},
    }
