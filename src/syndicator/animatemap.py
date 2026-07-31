"""Render an animated journey map MP4 from clustered positions.

Ported from the Go ``animatemap`` tool. Fetches OSM tiles for a base map,
animates logo markers flying in and bouncing, then assembles frames with ffmpeg.
"""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

from .journeymap import JourneyMap, Position

log = logging.getLogger(__name__)

IMG_WIDTH = 900
IMG_HEIGHT = 500
FPS = 24
FLY_IN_FRAMES = 20
FLY_IN_OVERLAP = 15
FLY_IN_SCALE = 3.0
BOUNCE_FRAMES = 12
BOUNCE_AMP = 0.25
MIN_HOLD_FRAMES = 6
MAX_HOLD_FRAMES = 48
FINAL_HOLD = 60

_TILE_SIZE = 256
_OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_USER_AGENT = "syndicator/0.2 (https://github.com/bbaumgartner/syndicator; journey map)"


def load_logo() -> Image.Image:
    path = files("syndicator") / "assets" / "logo.png"
    with path.open("rb") as f:
        return Image.open(f).convert("RGBA")


def marker_size(days: int) -> int:
    """Linearly interpolate between 30px (1 day) and 100px (≥30 days)."""
    return _linear_interp(days, 30, 100)


def hold_frames_for_days(days: int) -> int:
    return _linear_interp(days, MIN_HOLD_FRAMES, MAX_HOLD_FRAMES)


def bounce_multiplier(f: int, total: int, n_bounces: int, amplitude: float) -> float:
    """Size multiplier for bounce frame ``f``; 1.0 at both endpoints."""
    if total <= 0:
        return 1.0
    t = f / total
    damped_sine = math.sin(n_bounces * math.pi * t) * (1 - t)
    return 1 - amplitude * damped_sine


def _linear_interp(days: int, min_val: int, max_val: int) -> int:
    min_days, max_days = 1, 30
    if days <= min_days:
        return min_val
    if days >= max_days:
        return max_val
    t = (days - min_days) / (max_days - min_days)
    return int(round(min_val + t * (max_val - min_val)))


def mercator_y(lat: float) -> float:
    """Web Mercator y in [0, 1]; 0 = north pole, 1 = south pole."""
    lat_rad = lat * math.pi / 180
    return (1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2


def lat_lng_to_pixel(
    lat: float,
    lng: float,
    zoom: int,
    center_lat: float,
    center_lng: float,
    img_w: int,
    img_h: int,
) -> tuple[float, float]:
    world_size = _TILE_SIZE * (2**zoom)
    x = (lng + 180) / 360 * world_size - (center_lng + 180) / 360 * world_size + img_w / 2
    y = mercator_y(lat) * world_size - mercator_y(center_lat) * world_size + img_h / 2
    return x, y


def choose_bounds_and_zoom(
    positions: list[Position],
    img_w: int,
    img_h: int,
) -> tuple[float, float, int]:
    """Arithmetic centre + largest zoom (≤15) where all points fit with padding."""
    if not positions:
        return 0.0, 0.0, 1

    min_lat = max_lat = positions[0].lat
    min_lng = max_lng = positions[0].lng
    for p in positions[1:]:
        min_lat = min(min_lat, p.lat)
        max_lat = max(max_lat, p.lat)
        min_lng = min(min_lng, p.lng)
        max_lng = max(max_lng, p.lng)

    center_lat = (min_lat + max_lat) / 2
    center_lng = (min_lng + max_lng) / 2

    padding = 80.0
    for z in range(15, 0, -1):
        if all(
            padding <= x <= img_w - padding and padding <= y <= img_h - padding
            for p in positions
            for x, y in [lat_lng_to_pixel(p.lat, p.lng, z, center_lat, center_lng, img_w, img_h)]
        ):
            return center_lat, center_lng, z
    return center_lat, center_lng, 1


def render_base_map(
    center_lat: float,
    center_lng: float,
    zoom: int,
    *,
    img_w: int = IMG_WIDTH,
    img_h: int = IMG_HEIGHT,
    client: httpx.Client | None = None,
) -> Image.Image:
    """Fetch OSM tiles and composite a base map centred at the given point."""
    world_size = _TILE_SIZE * (2**zoom)
    cx = (center_lng + 180) / 360 * world_size
    cy = mercator_y(center_lat) * world_size
    left = cx - img_w / 2
    top = cy - img_h / 2

    x0 = int(math.floor(left / _TILE_SIZE))
    y0 = int(math.floor(top / _TILE_SIZE))
    x1 = int(math.floor((left + img_w - 1) / _TILE_SIZE))
    y1 = int(math.floor((top + img_h - 1) / _TILE_SIZE))
    n = 2**zoom

    canvas = Image.new("RGBA", (img_w, img_h))
    own_client = client is None
    if own_client:
        client = httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=30.0)
    assert client is not None
    try:
        for ty in range(y0, y1 + 1):
            if ty < 0 or ty >= n:
                continue
            for tx in range(x0, x1 + 1):
                wrapped_x = tx % n
                url = _OSM_TILE_URL.format(z=zoom, x=wrapped_x, y=ty)
                resp = client.get(url)
                resp.raise_for_status()
                tile = Image.open(BytesIO(resp.content)).convert("RGBA")
                px = int(tx * _TILE_SIZE - left)
                py = int(ty * _TILE_SIZE - top)
                canvas.paste(tile, (px, py))
    finally:
        if own_client:
            client.close()
    return canvas


def scale_image(src: Image.Image, size: int) -> Image.Image:
    """Scale ``src`` to a square of ``size`` px (Catmull-Rom / bicubic)."""
    return src.resize((size, size), Image.Resampling.BICUBIC)


def draw_marker(frame: Image.Image, scaled: Image.Image, px: float, py: float) -> None:
    """Alpha-composite a logo centred at pixel (px, py), clipped to the frame."""
    dx = int(round(px)) - scaled.width // 2
    dy = int(round(py)) - scaled.height // 2
    src_x = max(0, -dx)
    src_y = max(0, -dy)
    dst_x = max(0, dx)
    dst_y = max(0, dy)
    width = min(scaled.width - src_x, frame.width - dst_x)
    height = min(scaled.height - src_y, frame.height - dst_y)
    if width <= 0 or height <= 0:
        return
    cropped = scaled.crop((src_x, src_y, src_x + width, src_y + height))
    frame.paste(cropped, (dst_x, dst_y), cropped)


def position_start_frames(positions: list[Position]) -> list[int]:
    """Global frame index where each position's fly-in begins."""
    if not positions:
        return []
    offset = FLY_IN_FRAMES - FLY_IN_OVERLAP
    return [i * offset for i in range(len(positions))]


def total_frames(positions: list[Position]) -> int:
    if not positions:
        return FINAL_HOLD
    starts = position_start_frames(positions)
    last = len(positions) - 1
    last_end = starts[last] + FLY_IN_FRAMES + BOUNCE_FRAMES + hold_frames_for_days(positions[last].days)
    return last_end + FINAL_HOLD


@dataclass
class _MarkerState:
    px: float
    py: float
    final_size: int
    final_logo: Image.Image


def generate_animation(
    journey: JourneyMap,
    output_path: Path | str,
    *,
    base_map: Image.Image | None = None,
    logo: Image.Image | None = None,
) -> None:
    """Render ``journey`` to an H.264 MP4 at ``output_path``.

    ``base_map`` and ``logo`` may be injected for tests (skips OSM fetch / asset load).
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on $PATH. Install it with: brew install ffmpeg")

    output_path = Path(output_path)
    if not journey.positions:
        log.info("No positions found, skipping animation")
        return

    if logo is None:
        logo = load_logo()

    center_lat, center_lng, zoom = choose_bounds_and_zoom(
        journey.positions, IMG_WIDTH, IMG_HEIGHT
    )
    if base_map is None:
        log.info("Rendering base map (zoom %d, centre %.4f,%.4f)...", zoom, center_lat, center_lng)
        base_map = render_base_map(center_lat, center_lng, zoom)
    else:
        base_map = base_map.convert("RGBA").resize((IMG_WIDTH, IMG_HEIGHT), Image.Resampling.BICUBIC)

    states: list[_MarkerState] = []
    for p in journey.positions:
        px, py = lat_lng_to_pixel(
            p.lat, p.lng, zoom, center_lat, center_lng, IMG_WIDTH, IMG_HEIGHT
        )
        fs = marker_size(p.days)
        states.append(
            _MarkerState(px=px, py=py, final_size=fs, final_logo=scale_image(logo, fs))
        )

    starts = position_start_frames(journey.positions)
    total = total_frames(journey.positions)
    scaled_cache: dict[int, Image.Image] = {}

    def cached_scale(size: int) -> Image.Image:
        if size not in scaled_cache:
            scaled_cache[size] = scale_image(logo, size)
        return scaled_cache[size]

    with tempfile.TemporaryDirectory(prefix="animatemap_") as tmp:
        tmp_dir = Path(tmp)
        for global_f in range(total):
            if global_f % 30 == 0:
                log.info("  frame %d / %d", global_f, total)
            frame = base_map.copy()

            for i, start in enumerate(starts):
                local_f = global_f - start
                if local_f < 0:
                    continue
                st = states[i]
                anim_len = FLY_IN_FRAMES + BOUNCE_FRAMES + hold_frames_for_days(journey.positions[i].days)

                if local_f >= anim_len:
                    draw_marker(frame, st.final_logo, st.px, st.py)
                    continue
                if local_f < FLY_IN_FRAMES:
                    t = 1.0 if FLY_IN_FRAMES <= 1 else local_f / (FLY_IN_FRAMES - 1)
                    scale = FLY_IN_SCALE - t * t * (FLY_IN_SCALE - 1)
                    size = int(round(st.final_size * scale))
                elif local_f < FLY_IN_FRAMES + BOUNCE_FRAMES:
                    mult = bounce_multiplier(local_f - FLY_IN_FRAMES, BOUNCE_FRAMES, 3, BOUNCE_AMP)
                    size = int(round(st.final_size * mult))
                else:
                    draw_marker(frame, st.final_logo, st.px, st.py)
                    continue

                if size < 1:
                    size = 1
                draw_marker(frame, cached_scale(size), st.px, st.py)

            frame.save(tmp_dir / f"frame_{global_f:04d}.png")

        log.info("Assembling %d frames into %s...", total, output_path)
        _run_ffmpeg(tmp_dir, output_path)


def _run_ffmpeg(frames_dir: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pattern = str(frames_dir / "frame_%04d.png")
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(FPS),
        "-i",
        pattern,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "23",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr}")
