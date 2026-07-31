"""Build journey.json from Logseq journals, then animate it with animatemap.

Position extraction (formerly the Go ``journeymap`` tool) lives here in Python.
The map animation still shells out to the Go ``animatemap`` binary from the
converter repo.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

# ~0.8–1.1 km — covers same harbour / adjacent anchorages.
CLUSTER_THRESHOLD_DEG = 0.01

_POSITION_RE = re.compile(
    r"(?i)current-position::\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)"
)


@dataclass(frozen=True)
class Position:
    date: str
    lat: float
    lng: float
    days: int


@dataclass
class JourneyMap:
    positions: list[Position]


@dataclass(frozen=True)
class _JournalEntry:
    date: date
    lat: float
    lng: float


@dataclass(frozen=True)
class _EntryWithDays:
    entry: _JournalEntry
    days: int


def extract_positions(journals_dir: Path | str, *, today: date | None = None) -> JourneyMap:
    """Scan ``*.md`` journals for ``current-position::`` and return clustered stops."""
    journals_dir = Path(journals_dir)
    entries: list[_JournalEntry] = []

    for path in journals_dir.glob("*.md"):
        date_str = path.stem.replace("_", "-")
        try:
            entry_date = date.fromisoformat(date_str)
        except ValueError:
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as err:
            log.warning("could not read %s: %s", path, err)
            continue

        match = _POSITION_RE.search(content)
        if match is None:
            continue

        try:
            lat = float(match.group(1))
            lng = float(match.group(2))
        except ValueError:
            continue

        entries.append(_JournalEntry(date=entry_date, lat=lat, lng=lng))

    entries.sort(key=lambda e: e.date)

    if today is None:
        today = datetime.now().astimezone().date()

    with_days: list[_EntryWithDays] = []
    for i, entry in enumerate(entries):
        next_date = entries[i + 1].date if i + 1 < len(entries) else today
        days = (next_date - entry.date).days
        if days < 1:
            days = 1
        with_days.append(_EntryWithDays(entry=entry, days=days))

    return cluster_positions(with_days)


def cluster_positions(entries: list[_EntryWithDays]) -> JourneyMap:
    """Merge consecutive entries within ``CLUSTER_THRESHOLD_DEG`` into one stop."""

    @dataclass
    class _Cluster:
        lat: float
        lng: float
        date: date
        total_days: int

    clusters: list[_Cluster] = []
    for item in entries:
        e = item.entry
        if (
            clusters
            and abs(e.lat - clusters[-1].lat) <= CLUSTER_THRESHOLD_DEG
            and abs(e.lng - clusters[-1].lng) <= CLUSTER_THRESHOLD_DEG
        ):
            clusters[-1].total_days += item.days
            log.info(
                "Merging (%.5f, %.5f) on %s into cluster at (%.5f, %.5f) — combined %d days",
                e.lat,
                e.lng,
                e.date.isoformat(),
                clusters[-1].lat,
                clusters[-1].lng,
                clusters[-1].total_days,
            )
        else:
            clusters.append(
                _Cluster(lat=e.lat, lng=e.lng, date=e.date, total_days=item.days)
            )

    return JourneyMap(
        positions=[
            Position(
                date=c.date.isoformat(),
                lat=c.lat,
                lng=c.lng,
                days=c.total_days,
            )
            for c in clusters
        ]
    )


def write_journey_json(journey: JourneyMap, output_path: Path | str) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"positions": [asdict(p) for p in journey.positions]}
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _resolve_animatemap(cfg: Config) -> list[str] | None:
    configured = cfg.local.animatemap_bin
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return [str(path)]
        log.warning("configured animatemap binary missing: %s", path)

    repo = cfg.local.converter_repo_dir
    if repo is None or not Path(repo).exists():
        log.error("converter_repo_dir not configured/found — cannot run animatemap")
        return None

    bin_dir = cfg.repo_root / "bin"
    cached = bin_dir / "animatemap"
    if cached.exists():
        return [str(cached)]

    if shutil.which("go"):
        bin_dir.mkdir(exist_ok=True)
        try:
            subprocess.run(
                ["go", "build", "-o", str(cached), "./cmd/animatemap"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
            log.info("built animatemap -> %s", cached)
            return [str(cached)]
        except subprocess.CalledProcessError as err:
            log.error("go build animatemap failed: %s", err.stderr)
            return ["go", "run", "./cmd/animatemap"]

    return None


def generate_journey_map(cfg: Config, out_mp4: Path) -> bool:
    """Extract positions from journals and render ``out_mp4`` via animatemap."""
    am = _resolve_animatemap(cfg)
    if am is None:
        return False

    journey = extract_positions(cfg.journals_dir)
    if not journey.positions:
        log.info("no journey positions found — skipping animation")
        return False

    cwd = cfg.local.converter_repo_dir if am[0] == "go" else None
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            journey_json = Path(tmp) / "journey.json"
            write_journey_json(journey, journey_json)
            log.info("Wrote %d journey positions to %s", len(journey.positions), journey_json)
            result = subprocess.run(
                am + [str(journey_json), str(out_mp4)],
                check=True,
                capture_output=True,
                text=True,
                cwd=cwd,
            )
            if result.stdout.strip():
                log.info("animatemap: %s", result.stdout.strip().splitlines()[-1])
        return out_mp4.exists()
    except subprocess.CalledProcessError as err:
        log.error("journey map generation failed: %s\n%s", err, err.stderr)
        return False
