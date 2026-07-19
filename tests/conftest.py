"""Shared test helpers."""

from pathlib import Path

from syndicator.config import Config, LocalConfig, SharedConfig
from syndicator.llm import LLMClient

FIXTURES = Path(__file__).parent / "fixtures"
REEL_VIDEO_45 = {"aspect": "4:5", "width": 1080, "height": 1350, "max_seconds": 90, "pad_mode": "crop"}


class FakeLLM(LLMClient):
    """Injected LLM strategy for tests: canned outputs, no network, no cost."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def complete_text(self, node, model, system, user, temperature=None):
        self.calls += 1
        return f"[{node}] {user}"

    def complete_structured(self, node, model, system, user_content, schema, temperature=None):
        self.calls += 1
        return schema()  # e.g. CropFocus() -> centered focus


def make_cfg(tmp_path: Path) -> Config:
    """Config with a temp saillog (populated from fixtures)."""
    saillog = tmp_path / "saillog"
    (saillog / "journals").mkdir(parents=True)
    (saillog / "pages").mkdir(parents=True)
    for f in (FIXTURES / "journals").glob("*.md"):
        (saillog / "journals" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    for f in (FIXTURES / "pages").glob("*.md"):
        (saillog / "pages" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")

    shared = SharedConfig.model_validate(
        {
            "site": {"base_url": "https://example.org", "default_language": "en"},
            "sftp": {"host": "staging.example", "port": 22, "user": "sftp", "base_dir": "/syndicator"},
            "webhooks": {
                "publish_url": "https://n8n.example/webhook/publish",
                "reel_url": "https://n8n.example/webhook/reel",
            },
            "channels": {
                "hugo": {
                    "kind": "site",
                    "image": {"mode": "copy"},
                    "video": {"aspect": "16:9", "width": 1920, "height": 1080, "pad_mode": "crop"},
                },
                "facebook": {
                    "kind": "social",
                    "image": {"mode": "convert", "max_edge": 2048},
                    "video": {"max_seconds": 240},
                    "reel_video": {"aspect": "4:5", "width": 1080, "height": 1350, "max_seconds": 240, "pad_mode": "crop"},
                },
                "instagram": {
                    "kind": "social",
                    "image": {"mode": "convert", "aspect": "4:5", "width": 1080, "height": 1350},
                    "video": {"aspect": "4:5", "width": 1080, "height": 1350, "max_seconds": 90, "pad_mode": "crop"},
                    "reel_video": REEL_VIDEO_45,
                },
                "x": {
                    "kind": "social",
                    "image": {"mode": "convert", "max_edge": 2048},
                    "video": {"max_seconds": 140},
                    "reel_video": {"aspect": "4:5", "width": 1080, "height": 1350, "max_seconds": 140, "pad_mode": "crop"},
                },
            },
        }
    )
    local = LocalConfig(saillog_dir=saillog, sftp_key=tmp_path / "sftp_key")
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
