"""journeymap node: wrap the proven Go tools from logseq-to-hugo-converter.

``cmd/journeymap`` scans journals for current-position:: entries and writes
data/journey.json; ``cmd/animatemap`` renders static/journey-map.mp4. Both
are deterministic and battle-tested — we build them once and call them as
subprocesses instead of porting them.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from ..config import Config

log = logging.getLogger(__name__)


def _resolve_binary(cfg: Config, name: str, configured: str) -> list[str] | None:
    """Return the command prefix for a Go tool: configured binary, cached
    build, fresh build, or `go run` fallback."""
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return [str(path)]
        log.warning("configured %s binary missing: %s", name, path)

    repo = cfg.local.converter_repo_dir
    if repo is None or not Path(repo).exists():
        log.error("converter_repo_dir not configured/found — cannot run %s", name)
        return None

    bin_dir = cfg.repo_root / "bin"
    cached = bin_dir / name
    if cached.exists():
        return [str(cached)]

    if shutil.which("go"):
        bin_dir.mkdir(exist_ok=True)
        try:
            subprocess.run(
                ["go", "build", "-o", str(cached), f"./cmd/{name}"],
                cwd=repo, check=True, capture_output=True, text=True,
            )
            log.info("built %s -> %s", name, cached)
            return [str(cached)]
        except subprocess.CalledProcessError as err:
            log.error("go build %s failed: %s", name, err.stderr)

    if shutil.which("go"):
        return ["go", "run", f"./cmd/{name}"]
    return None


def generate_journey_map(cfg: Config) -> bool:
    """Regenerate data/journey.json and static/journey-map.mp4 in the site repo."""
    journey_json = cfg.local.sailingnomads_dir / "data" / "journey.json"
    journey_mp4 = cfg.local.sailingnomads_dir / "static" / "journey-map.mp4"

    jm = _resolve_binary(cfg, "journeymap", cfg.local.journeymap_bin)
    am = _resolve_binary(cfg, "animatemap", cfg.local.animatemap_bin)
    if jm is None or am is None:
        return False

    cwd = cfg.local.converter_repo_dir if jm[0] == "go" or am[0] == "go" else None
    try:
        journey_json.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            jm + [str(cfg.journals_dir), str(journey_json)],
            check=True, capture_output=True, text=True, cwd=cwd,
        )
        if not journey_json.exists():
            log.info("no journey positions found — skipping animation")
            return True
        journey_mp4.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            am + [str(journey_json), str(journey_mp4)],
            check=True, capture_output=True, text=True, cwd=cwd,
        )
        if result.stdout.strip():
            log.info("animatemap: %s", result.stdout.strip().splitlines()[-1])
        return True
    except subprocess.CalledProcessError as err:
        log.error("journey map generation failed: %s\n%s", err, err.stderr)
        return False
