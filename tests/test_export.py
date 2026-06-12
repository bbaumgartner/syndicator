"""End-to-end tests for the social pipeline (dry-run, no network)."""

from pathlib import Path

from PIL import Image

from syndicator.model import PackageManifest
from syndicator.nodes.extract import scan_blog_posts
from syndicator.pipeline import next_catchup_post, run_social_for_post
from syndicator.state import StateStore

from conftest import make_cfg


def create_real_assets(posts) -> None:
    """Create real (tiny) images/videos so media adaptation can run."""
    for post in posts:
        for media in post.all_media():
            if media.kind == "youtube" or media.source_path is None:
                continue
            media.source_path.parent.mkdir(parents=True, exist_ok=True)
            if media.source_path.exists():
                continue
            if media.kind == "image":
                Image.new("RGB", (1600, 900), (30, 90, 160)).save(media.source_path)
            else:
                # Keep "videos" as dummy files; ffmpeg handling is covered in
                # test_media_adapt. Here they will fail adaptation gracefully.
                media.source_path.write_bytes(b"not a real video")


def test_run_social_dry_run_creates_packages_and_review(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-06-10_Griechenland_❤️"]
    create_real_assets([post])

    export_dir = run_social_for_post(cfg, post, dry_run=True, verify_links=False)
    assert export_dir == cfg.exports_dir / post.slug

    review = export_dir / "review.html"
    assert review.exists()
    html = review.read_text(encoding="utf-8")
    assert "Griechenland" in html
    assert "Copy caption" in html

    # 3 channels x (intro + 3 sections) packages
    manifests = list(export_dir.glob("*/*/package.json"))
    assert len(manifests) == 12

    m = PackageManifest.model_validate_json(
        (export_dir / "instagram" / "01-gastfreundschaft" / "package.json").read_text(encoding="utf-8")
    )
    assert m.kind == "section"
    assert m.language == "en"
    assert m.link.startswith("https://example.org/posts/")
    # Instagram captions never contain the URL.
    assert "https://" not in m.text or m.text.find("https://") > len(m.text)

    caption = (export_dir / "facebook" / "00-intro" / "caption.txt").read_text(encoding="utf-8")
    assert "[dry-run caption facebook" in caption
    assert "https://example.org/posts/" in caption  # inline link appended

    # Adapted images: instagram packages contain 1080x1350 crops.
    ig_images = list((export_dir / "instagram" / "01-gastfreundschaft").glob("*.jpg"))
    assert ig_images
    with Image.open(ig_images[0]) as im:
        assert im.size == (1080, 1350)

def test_dry_run_does_not_mark_state(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-05-19_Charly_Superstar"]

    run_social_for_post(cfg, post, dry_run=True, verify_links=False)
    store = StateStore(cfg.state_dir)
    assert store.load(post.slug).channel("facebook").status == "pending"


def test_real_run_marks_exported_and_catchup_order(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    store = StateStore(cfg.state_dir)

    first = next_catchup_post(cfg, store)
    assert first is not None
    assert first.slug == "2024-06-14_Renan"  # oldest post first

    # Simulate Renan already published (bootstrap behavior).
    for channel in ("facebook", "instagram", "x"):
        store.mark(first.slug, channel, "published")
    second = next_catchup_post(cfg, store)
    assert second.slug == "2026-01-17_Frühlingspläne_2026"

    # Run with a non-dry LLM in dry mode? No — use dry_run=False but with a
    # dry LLM is not possible via run_social_for_post; instead mark manually:
    # we only verify the state transition contract here.
    store.mark(second.slug, "facebook", "exported", source_hash="sha256:x")
    assert store.load(second.slug).channel("facebook").status == "exported"
    # Still pending channels left -> same post remains next in the backlog.
    assert next_catchup_post(cfg, store).slug == second.slug
