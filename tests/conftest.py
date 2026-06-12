"""Shared test helpers."""

from pathlib import Path

from syndicator.config import Config, LocalConfig, SharedConfig

FIXTURES = Path(__file__).parent / "fixtures"


def make_cfg(tmp_path: Path) -> Config:
    """Config with a temp saillog (populated from fixtures) and a temp Hugo site."""
    saillog = tmp_path / "saillog"
    (saillog / "journals").mkdir(parents=True)
    (saillog / "pages").mkdir(parents=True)
    for f in (FIXTURES / "journals").glob("*.md"):
        (saillog / "journals" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    for f in (FIXTURES / "pages").glob("*.md"):
        (saillog / "pages" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")

    site = tmp_path / "site"
    (site / "content" / "posts").mkdir(parents=True)

    shared = SharedConfig.model_validate(
        {
            "site": {"base_url": "https://example.org"},
            "channels": {
                "hugo": {"kind": "site"},
                "facebook": {"kind": "social"},
                "instagram": {"kind": "social", "link_mode": "bio"},
                "x": {"kind": "social", "max_media_per_post": 4, "max_chars": 280},
                "substack": {"kind": "article", "enabled": False},
                "medium": {"kind": "article", "enabled": False},
            },
        }
    )
    local = LocalConfig(saillog_dir=saillog, sailingnomads_dir=site, runs_dir=str(tmp_path / "runs"))
    return Config(shared=shared, local=local, repo_root=tmp_path)


def create_dummy_assets(posts) -> None:
    """Create placeholder files for all media referenced by the given posts."""
    for post in posts:
        for media in post.all_media():
            if media.kind == "youtube" or media.source_path is None:
                continue
            media.source_path.parent.mkdir(parents=True, exist_ok=True)
            if not media.source_path.exists():
                media.source_path.write_bytes(b"dummy")
