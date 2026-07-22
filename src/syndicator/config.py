"""Configuration loading.

Two layers:
- syndicator.yaml      shared, committed, identical on both machines
- config.local.yaml    machine-specific paths, gitignored

The repo root is located by walking up from this file, so the CLI works from
any working directory.

v2 (n8n migration): translation, captions and the Hugo render moved to n8n;
the final site commit is manual. Their models/prompts are no longer configured
here. What stays local: media specs (adaptation), the crop-focus vision model,
the SFTP staging target and the n8n webhook URLs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

ChannelKind = Literal["site", "social", "article"]


class SiteConfig(BaseModel):
    title: str = "Sailing Nomads"
    base_url: str
    default_language: str = "en"


class CropFocusConfig(BaseModel):
    enabled: bool = True
    model: str = "gpt-5.4-mini"


class MediaConfig(BaseModel):
    crop_focus: CropFocusConfig = CropFocusConfig()


class SftpConfig(BaseModel):
    host: str
    port: int = 22
    user: str = "sftp"
    base_dir: str = "/syndicator"


class WebhooksConfig(BaseModel):
    publish_url: str = ""
    reel_url: str = ""


class ImageSpec(BaseModel):
    mode: Literal["copy", "convert"] = "convert"
    aspect: str | None = None  # e.g. "4:5"; None = keep aspect
    width: int | None = None
    height: int | None = None
    max_edge: int | None = None
    format: str = "jpeg"
    quality: int = 90


class VideoSpec(BaseModel):
    aspect: str | None = None  # e.g. "9:16"; None = keep aspect
    width: int | None = None
    height: int | None = None
    max_seconds: int | None = None
    pad_mode: Literal["blur", "black", "crop"] = "crop"


class ChannelConfig(BaseModel):
    kind: ChannelKind
    enabled: bool = True
    image: ImageSpec = ImageSpec()
    video: VideoSpec = VideoSpec()
    reel_video: VideoSpec | None = None  # 4:5 crop for reel-format posts


class SharedConfig(BaseModel):
    site: SiteConfig
    media: MediaConfig = MediaConfig()
    sftp: SftpConfig
    webhooks: WebhooksConfig = WebhooksConfig()
    channels: dict[str, ChannelConfig]


class LocalConfig(BaseModel):
    # Logseq graph, synced between machines via Syncthing.
    saillog_dir: Path
    # Private key for the chrooted SFTP staging user.
    sftp_key: Path
    # Old converter repo: source for the journeymap/animatemap Go tools.
    converter_repo_dir: Path | None = None
    journeymap_bin: str = ""
    animatemap_bin: str = ""


class Config(BaseModel):
    shared: SharedConfig
    local: LocalConfig
    repo_root: Path = REPO_ROOT

    # --- derived paths -------------------------------------------------

    @property
    def journals_dir(self) -> Path:
        return self.local.saillog_dir / "journals"

    @property
    def pages_dir(self) -> Path:
        return self.local.saillog_dir / "pages"

    def social_channels(self) -> dict[str, ChannelConfig]:
        """Enabled social channels, in YAML order (pyyaml preserves it)."""
        return {
            name: ch
            for name, ch in self.shared.channels.items()
            if ch.kind == "social" and ch.enabled
        }


def load_config(repo_root: Path | None = None) -> Config:
    root = repo_root or REPO_ROOT
    shared_path = root / "syndicator.yaml"
    local_path = root / "config.local.yaml"

    if not shared_path.exists():
        raise FileNotFoundError(f"missing {shared_path}")
    if not local_path.exists():
        raise FileNotFoundError(
            f"missing {local_path} — copy config.local.yaml.example and adjust paths"
        )

    shared = SharedConfig.model_validate(yaml.safe_load(shared_path.read_text()))
    local_raw = yaml.safe_load(local_path.read_text()) or {}
    if isinstance(local_raw.get("sftp_key"), str):
        local_raw["sftp_key"] = str(Path(local_raw["sftp_key"]).expanduser())
    local = LocalConfig.model_validate(local_raw)
    return Config(shared=shared, local=local, repo_root=root)
