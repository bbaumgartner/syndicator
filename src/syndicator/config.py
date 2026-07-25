"""Configuration loading.

Two layers:
- syndicator.yaml      shared, committed, identical on both machines
- config.local.yaml    machine-specific paths, gitignored

The repo root is located by walking up from this file, so the CLI works from
any working directory.

v2: translation, captions, media adaptation and the Hugo render live in n8n;
the final site commit is manual. Local config keeps the SFTP staging target and
the webhook URLs. Social/Hugo media geometry is hardcoded in the n8n Adapt
workflows.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class SiteConfig(BaseModel):
    title: str = "Sailing Nomads"
    base_url: str
    default_language: str = "en"


class SftpConfig(BaseModel):
    host: str
    port: int = 22
    user: str = "sftp"
    base_dir: str = "/syndicator"


class WebhooksConfig(BaseModel):
    publish_url: str = ""
    reel_url: str = ""


class SharedConfig(BaseModel):
    site: SiteConfig
    sftp: SftpConfig
    webhooks: WebhooksConfig = WebhooksConfig()


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
