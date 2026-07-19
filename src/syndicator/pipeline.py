"""Pipeline orchestration for the two v2 commands: ``syndicate`` and ``redeploy``.

The local client is a thin trigger (§3): it extracts the diary, adapts media,
uploads it over SFTP and fires the n8n webhooks. It holds no state beyond the
``syndicated-at::`` marker; all heavy lifting (translate, render, commit,
captions, drafts) happens in n8n.

``syndicate``: for every ``status:: online`` post without a marker (or one
``--post``): the global journey map is generated + uploaded once per invocation;
then per post — enforce a header, adapt media, SFTP upload, N× ``/reel``,
``/publish`` (redeploy=false), set the marker once all webhooks are accepted.

``redeploy --post``: site-only re-render (site media + journey map → SFTP →
``/publish`` with redeploy=true). No social, no marker.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .llm import LLMClient
from .marker import is_syndicated, set_syndicated_at
from .model import BlogPost
from .nodes.extract import scan_blog_posts
from .nodes.journeymap import generate_journey_map
from .payload import (
    build_publish_payload,
    build_reel_payload,
    build_site_media,
    journey_map_remote,
)
from .sftp_upload import SftpUploader, sftp_session
from .staging import StagedPost, stage_post
from .webhook import WebhookError, post_webhook

log = logging.getLogger(__name__)


@dataclass
class SyndicateReport:
    done: list[str] = field(default_factory=list)
    skipped_marked: list[str] = field(default_factory=list)
    skipped_no_header: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (slug, reason)


def _scan_online(cfg: Config) -> list[BlogPost]:
    return scan_blog_posts(cfg.journals_dir, cfg.pages_dir, online_only=True)


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
    """Syndicate new online posts (or one ``--post``)."""
    _check_webhooks(cfg, need_reel=True)
    llm = LLMClient()
    posts = _scan_online(cfg)
    if slug is not None:
        posts = [p for p in posts if p.slug == slug]
        if not posts:
            raise SystemExit(f"unknown online post slug: {slug}")

    report = SyndicateReport()
    with tempfile.TemporaryDirectory(prefix="syndicator-") as tmp_root:
        tmp = Path(tmp_root)
        jm_path = tmp / "journey-map.mp4"
        has_journey_map = generate_journey_map(cfg, jm_path)

        with sftp_session(cfg) as sftp:
            if has_journey_map:
                sftp.upload(jm_path, journey_map_remote(cfg))
            for post in posts:
                _syndicate_one(cfg, llm, sftp, post, tmp, has_journey_map, report)

    _log_report(report)
    return report


def _syndicate_one(
    cfg: Config,
    llm: LLMClient,
    sftp: SftpUploader,
    post: BlogPost,
    tmp: Path,
    has_journey_map: bool,
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
        staged = stage_post(post, cfg, llm, tmp / slug, include_social=True)
        _upload_all(sftp, staged)

        for sv in staged.videos:
            if not sv.reels:
                log.warning("%s: video %d has no reel — skipping /reel", slug, sv.index)
                continue
            payload = build_reel_payload(
                post, cfg,
                index=sv.index, section_title=sv.section_title,
                section_text=sv.section_text, alt=sv.alt,
                reels=sv.reels, covers=sv.covers,
            )
            post_webhook(cfg.shared.webhooks.reel_url, payload, label=f"/reel {slug} #{sv.index}")

        site_media = build_site_media(post, cfg, include_journey_map=has_journey_map)
        publish = build_publish_payload(
            post, cfg, site_media=site_media, header=staged.header, redeploy=False
        )
        post_webhook(cfg.shared.webhooks.publish_url, publish, label=f"/publish {slug}")

        set_syndicated_at(post)
        report.done.append(slug)
    except (WebhookError, OSError) as err:
        log.error("%s: syndication failed — %s (marker not set; will retry next run)", slug, err)
        report.failed.append((slug, str(err)))


def redeploy(cfg: Config, slug: str) -> None:
    """Force a site-only redeploy of one post (re-render + re-translate + commit)."""
    _check_webhooks(cfg, need_reel=False)
    llm = LLMClient()
    posts = {p.slug: p for p in _scan_online(cfg)}
    if slug not in posts:
        known = "\n  ".join(sorted(posts))
        raise SystemExit(f"unknown online post slug: {slug}\nknown posts:\n  {known}")
    post = posts[slug]
    if not _has_header(post):
        raise SystemExit(f"{slug}: no header:: image — the site build requires a featured image")

    with tempfile.TemporaryDirectory(prefix="syndicator-") as tmp_root:
        tmp = Path(tmp_root)
        jm_path = tmp / "journey-map.mp4"
        has_journey_map = generate_journey_map(cfg, jm_path)
        staged = stage_post(post, cfg, llm, tmp / slug, include_social=False)

        with sftp_session(cfg) as sftp:
            if has_journey_map:
                sftp.upload(jm_path, journey_map_remote(cfg))
            _upload_all(sftp, staged)

            site_media = build_site_media(post, cfg, include_journey_map=has_journey_map)
            publish = build_publish_payload(
                post, cfg, site_media=site_media, header={}, redeploy=True
            )
            post_webhook(cfg.shared.webhooks.publish_url, publish, label=f"/publish redeploy {slug}")

    log.info("redeploy %s: site rebuild handed off (no social, no marker)", slug)


def _log_report(report: SyndicateReport) -> None:
    log.info(
        "syndicate: %d done, %d already-marked, %d no-header, %d failed",
        len(report.done), len(report.skipped_marked),
        len(report.skipped_no_header), len(report.failed),
    )
    for slug, reason in report.failed:
        log.error("  failed: %s (%s)", slug, reason)
    for slug in report.skipped_no_header:
        log.warning("  skipped (no header): %s", slug)
