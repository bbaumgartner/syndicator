"""export node: write social post packages and the review page.

Output layout (inside the Syncthing-synced data dir):

    <saillog>/.syndicator/exports/<slug>/
        review.html
        <channel>/<nn>-<kind>/
            caption.txt      copy-paste-ready final text
            package.json     PackageManifest
            <media files>    adapted for the channel
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..config import REPO_ROOT, Config
from ..llm import LLMClient
from ..model import BlogPost, PackageManifest, PostIntent, SocialDraft
from ..siteurl import resolve_post_url
from .caption import _youtube_links, compose_post_text, generate_caption
from .media_adapt import adapt_media_for_channel
from .social_plan import plan_social

log = logging.getLogger(__name__)


def _package_dirname(intent: PostIntent) -> str:
    if intent.kind == "intro":
        return f"{intent.index:02d}-intro"
    title = (intent.section_title or f"section-{intent.section_index}").lower()
    title = re.sub(r"[^\w]+", "-", title, flags=re.UNICODE).strip("-") or "section"
    return f"{intent.index:02d}-{title}"


def export_package(
    post: BlogPost,
    intent: PostIntent,
    draft: SocialDraft,
    url: str,
    cfg: Config,
    llm: LLMClient,
    export_dir: Path,
) -> PackageManifest:
    ch_cfg = cfg.shared.channels[intent.channel]
    pkg_dir = export_dir / intent.channel / _package_dirname(intent)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    media_files: list[str] = []
    for media in intent.media:
        out = adapt_media_for_channel(media, intent.channel, cfg, pkg_dir, llm)
        if out is not None:
            media_files.append(out.name)

    youtube = _youtube_links(post, intent)
    text = compose_post_text(draft, intent, ch_cfg, url, youtube)

    manifest = PackageManifest(
        slug=post.slug,
        channel=intent.channel,
        index=intent.index,
        kind=intent.kind,
        section_title=intent.section_title,
        text=text,
        hashtags=draft.hashtags,
        link=url,
        suggested_date=intent.suggested_date,
        media_files=media_files,
        youtube_links=youtube,
        language=ch_cfg.language,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        model=ch_cfg.caption_model if not llm.dry_run else "dry-run",
    )

    (pkg_dir / "caption.txt").write_text(text + "\n", encoding="utf-8")
    (pkg_dir / "package.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


def write_review_html(
    post: BlogPost,
    manifests: dict[str, list[PackageManifest]],
    links: dict[str, str],
    export_dir: Path,
) -> Path:
    env = Environment(loader=FileSystemLoader(REPO_ROOT / "templates"), autoescape=True)
    template = env.get_template("review.html.j2")

    channels = {
        channel: [
            {
                "index": m.index,
                "kind": m.kind,
                "section_title": m.section_title,
                "suggested_date": m.suggested_date,
                "text": m.text,
                "media_files": m.media_files,
                "youtube_links": m.youtube_links,
                "dir": f"{channel}/{_package_dirname_from_manifest(m)}",
            }
            for m in packages
        ]
        for channel, packages in manifests.items()
    }

    html = template.render(
        post_title=post.meta.title,
        post_date=post.meta.date,
        slug=post.slug,
        links=links,
        channels=channels,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    out = export_dir / "review.html"
    out.write_text(html, encoding="utf-8")
    return out


def _package_dirname_from_manifest(m: PackageManifest) -> str:
    intent = PostIntent(
        channel=m.channel, index=m.index,
        kind=m.kind if m.kind != "article" else "intro",
        section_index=None, section_title=m.section_title, media=[],
    )
    return _package_dirname(intent)


def export_social(
    post: BlogPost,
    cfg: Config,
    llm: LLMClient,
    channels: list[str] | None = None,
    verify_links: bool = True,
    start=None,
) -> Path:
    """Run the social pipeline for one post: plan, caption, adapt, package."""
    export_dir = cfg.exports_dir / post.slug
    export_dir.mkdir(parents=True, exist_ok=True)

    plans = plan_social(post, cfg, start)
    if channels is not None:
        plans = {c: intents for c, intents in plans.items() if c in channels}

    links: dict[str, str] = {}
    manifests: dict[str, list[PackageManifest]] = {}
    for channel, intents in plans.items():
        lang = cfg.shared.channels[channel].language
        if lang not in links:
            links[lang] = resolve_post_url(cfg, post.slug, lang, verify=verify_links and not llm.dry_run)
        url = links[lang]

        manifests[channel] = []
        for intent in intents:
            log.info("caption %s #%d (%s)", channel, intent.index, intent.kind)
            draft = generate_caption(post, intent, cfg, llm)
            manifest = export_package(post, intent, draft, url, cfg, llm, export_dir)
            manifests[channel].append(manifest)

    write_review_html(post, manifests, links, export_dir)
    log.info("export written to %s", export_dir)
    return export_dir
