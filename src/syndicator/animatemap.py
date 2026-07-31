"""Render an animated 3D journey globe MP4 from clustered positions.

PyVista offscreen sphere with a low-res base texture for wide shots and OSM
map tiles draped on the surface for sharp close-ups. Frames assembled with ffmpeg.
"""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from io import BytesIO
from pathlib import Path

import httpx
import numpy as np
import pyvista as pv
from PIL import Image

from .journeymap import JourneyMap, Position

log = logging.getLogger(__name__)

IMG_WIDTH = 900
IMG_HEIGHT = 500
# Render at 2× then Lanczos-downsample for sharper map text.
RENDER_SCALE = 2
FPS = 24
# Timing is intentionally snappy (~2× prior durations); holds are brief so legs blend.
MIN_HOLD_FRAMES = 0
MAX_HOLD_FRAMES = 4
INTRO_HOLD = 9
ZOOM_IN_FRAMES = 54
ZOOM_OUT_FRAMES = 54
OUTRO_HOLD = 24
TRAVEL_FRAMES_MIN = 18
TRAVEL_FRAMES_MAX = 48
PATH_SAMPLES = 64
PATH_COLOR = (0, 130, 210)
PATH_RADIUS = 0.0007
EARTH_RADIUS = 1.0
TILE_RADIUS = 1.0015
PATH_RADIUS_R = 1.003
# Great-circle legs lift into a sine arch (flight-path style).
PATH_ARCH_BASE = 0.012
PATH_ARCH_PER_DEG = 0.001
PATH_ARCH_MAX = 0.08
# Camera distance from Earth centre (larger = farther / zoomed out).
CAM_DIST_WIDE = 3.6
CAM_DIST_CLOSE_MIN = 1.10
CAM_DIST_CLOSE_MAX = 1.28
# Overview zoom: frame the journey mosaic tightly (not a full-globe pullback).
OVERVIEW_FRAME_MARGIN = 1.08
OVERVIEW_MIN_HALF_DEG = 1.8
CAMERA_VFOV_DEG = 30.0
LOOK_AHEAD = 0.0  # camera tracks the traveler exactly (look-ahead caused focus jumps)
PITCH_OFFSET_DEG = 2.5
MAX_TILES = 96
TILE_ZOOM_MIN = 4
TILE_ZOOM_MAX = 13
# Journey mosaic stays up through the route-overview outro.
DETAIL_DIST_MAX = 3.0
# Padding around the journey bbox when building the fixed detail mosaic.
JOURNEY_PAD_DEG = 0.6
JOURNEY_PAD_FRAC = 0.25
# Logo markers track the on-screen path width (not days-based size).
MARKER_PATH_SCALE = 5.0
MARKER_SIZE_MIN = 24
MARKER_SIZE_MAX =72
# Lower CRF = sharper text in the H.264 encode.
FFMPEG_CRF = 17

_OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_USER_AGENT = "syndicator/0.2 (https://github.com/bbaumgartner/syndicator; journey map)"

TileFetcher = Callable[[int, int, int], Image.Image]


def load_logo() -> Image.Image:
    path = files("syndicator") / "assets" / "logo.png"
    with path.open("rb") as f:
        return Image.open(f).convert("RGBA")


def load_earth_texture() -> Image.Image:
    path = files("syndicator") / "assets" / "earth.jpg"
    with path.open("rb") as f:
        return Image.open(f).convert("RGB")


def marker_size(days: int) -> int:
    """Kept for compatibility; route markers use ``route_marker_size`` instead."""
    return _linear_interp(days, 30, 100)


def hold_frames_for_days(days: int) -> int:
    return _linear_interp(days, MIN_HOLD_FRAMES, MAX_HOLD_FRAMES)


def travel_frames_for_distance(angular_deg: float) -> int:
    """More frames for longer great-circle legs."""
    t = min(1.0, max(0.0, angular_deg / 40.0))
    return int(round(TRAVEL_FRAMES_MIN + t * (TRAVEL_FRAMES_MAX - TRAVEL_FRAMES_MIN)))


def journey_span_deg(positions: list[Position]) -> float:
    """Max angular separation between any two stops."""
    if len(positions) < 2:
        return 0.0
    span = 0.0
    for i, a in enumerate(positions):
        for b in positions[i + 1 :]:
            span = max(span, angular_distance_deg(a.lat, a.lng, b.lat, b.lng))
    return span


def close_camera_distance(positions: list[Position]) -> float:
    """Closer for short regional spans, slightly farther for long journeys."""
    if len(positions) < 2:
        return CAM_DIST_CLOSE_MIN
    span = journey_span_deg(positions)
    t = min(1.0, max(0.0, (span - 5.0) / 35.0))
    return CAM_DIST_CLOSE_MIN + t * (CAM_DIST_CLOSE_MAX - CAM_DIST_CLOSE_MIN)


def journey_bbox_deg(positions: list[Position]) -> tuple[float, float, float, float]:
    """Return (lat_min, lat_max, lng_min, lng_max) including mosaic padding."""
    if not positions:
        return 0.0, 0.0, 0.0, 0.0
    lats = [p.lat for p in positions]
    lngs = [p.lng for p in positions]
    lat_min, lat_max = min(lats), max(lats)
    lng_min, lng_max = min(lngs), max(lngs)
    pad_lat = max(JOURNEY_PAD_DEG, (lat_max - lat_min) * JOURNEY_PAD_FRAC)
    pad_lng = max(JOURNEY_PAD_DEG, (lng_max - lng_min) * JOURNEY_PAD_FRAC)
    if lat_max - lat_min < 0.2:
        pad_lat = max(pad_lat, 0.5)
    if lng_max - lng_min < 0.2:
        pad_lng = max(pad_lng, 0.5)
    return (
        max(-85.0, lat_min - pad_lat),
        min(85.0, lat_max + pad_lat),
        lng_min - pad_lng,
        lng_max + pad_lng,
    )


def overview_camera_distance(positions: list[Position]) -> float:
    """Distance that tightly frames the journey mosaic (fills the viewport)."""
    close = close_camera_distance(positions)
    if not positions:
        return close
    lat_min, lat_max, lng_min, lng_max = journey_bbox_deg(positions)
    half_lat = max(OVERVIEW_MIN_HALF_DEG, (lat_max - lat_min) * 0.5)
    half_lng = max(OVERVIEW_MIN_HALF_DEG, (lng_max - lng_min) * 0.5)
    aspect = IMG_WIDTH / IMG_HEIGHT
    half_vfov = math.radians(CAMERA_VFOV_DEG / 2.0)
    half_hfov = math.atan(math.tan(half_vfov) * aspect)
    # Camera altitude so the mosaic just fills vertical and horizontal FOV.
    alt_lat = math.tan(math.radians(half_lat)) / math.tan(half_vfov)
    alt_lng = math.tan(math.radians(half_lng)) / math.tan(half_hfov)
    altitude = max(alt_lat, alt_lng) * OVERVIEW_FRAME_MARGIN
    dist = EARTH_RADIUS + altitude
    # Always a bit farther than the tracking shot, never a full-globe view.
    dist = max(close * 1.04, dist)
    dist = min(dist, CAM_DIST_WIDE * 0.55)
    return dist


def path_width_px(distance: float, img_h: int = IMG_HEIGHT) -> float:
    """Approximate on-screen width of the path tube in output pixels."""
    cam_to_surface = max(0.05, distance - EARTH_RADIUS)
    return (2.0 * PATH_RADIUS / cam_to_surface) * (img_h / 2.0) / math.tan(
        math.radians(CAMERA_VFOV_DEG / 2.0)
    )


def route_marker_size(distance: float, *, render_scale: int = 1) -> int:
    """Logo size matched to the visible path line thickness."""
    px = path_width_px(distance) * MARKER_PATH_SCALE * render_scale
    return max(MARKER_SIZE_MIN * render_scale, min(MARKER_SIZE_MAX * render_scale, int(round(px))))


def _ease_in_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def _linear_interp(days: int, min_val: int, max_val: int) -> int:
    min_days, max_days = 1, 30
    if days <= min_days:
        return min_val
    if days >= max_days:
        return max_val
    t = (days - min_days) / (max_days - min_days)
    return int(round(min_val + t * (max_val - min_val)))


# ---- sphere math -----------------------------------------------------------


def ll_to_xyz(lat: float, lng: float, radius: float = EARTH_RADIUS) -> np.ndarray:
    """Lat/lng (degrees) → vector of length ``radius``."""
    lat_r = math.radians(lat)
    lng_r = math.radians(lng)
    cl = math.cos(lat_r)
    return np.array(
        [cl * math.cos(lng_r), cl * math.sin(lng_r), math.sin(lat_r)],
        dtype=np.float64,
    ) * radius


def xyz_to_ll(v: np.ndarray) -> tuple[float, float]:
    """Vector → lat/lng (degrees)."""
    n = np.linalg.norm(v)
    if n < 1e-12:
        return 0.0, 0.0
    x, y, z = float(v[0] / n), float(v[1] / n), float(v[2] / n)
    lat = math.degrees(math.asin(max(-1.0, min(1.0, z))))
    lng = math.degrees(math.atan2(y, x))
    return lat, lng


def angular_distance_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    a = ll_to_xyz(lat1, lng1)
    b = ll_to_xyz(lat2, lng2)
    dot = float(np.clip(np.dot(a, b), -1.0, 1.0))
    return math.degrees(math.acos(dot))


def slerp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    """Spherical linear interpolation between vectors ``a`` and ``b``."""
    a_n = a / np.linalg.norm(a)
    b_n = b / np.linalg.norm(b)
    dot = float(np.clip(np.dot(a_n, b_n), -1.0, 1.0))
    omega = math.acos(dot)
    if omega < 1e-9:
        return a_n * np.linalg.norm(a)
    so = math.sin(omega)
    out = (math.sin((1 - t) * omega) * a_n + math.sin(t * omega) * b_n) / so
    return out * ((1 - t) * np.linalg.norm(a) + t * np.linalg.norm(b))


def great_circle_points(
    lat1: float,
    lng1: float,
    lat2: float,
    lng2: float,
    n: int,
) -> list[tuple[float, float]]:
    """Sample ``n`` points (inclusive) along the short great-circle arc."""
    if n < 2:
        return [(lat1, lng1)]
    a = ll_to_xyz(lat1, lng1)
    b = ll_to_xyz(lat2, lng2)
    return [xyz_to_ll(slerp(a, b, i / (n - 1))) for i in range(n)]


def path_arch_peak(angular_deg: float) -> float:
    """Peak radial lift above ``PATH_RADIUS_R`` for a leg of the given length."""
    peak = PATH_ARCH_BASE + PATH_ARCH_PER_DEG * max(0.0, angular_deg)
    return min(PATH_ARCH_MAX, peak)


def great_circle_arch_xyz(
    lat1: float,
    lng1: float,
    lat2: float,
    lng2: float,
    n: int,
) -> list[np.ndarray]:
    """Sample ``n`` XYZ points along a great-circle flight arch (sine lift)."""
    if n < 2:
        return [ll_to_xyz(lat1, lng1, PATH_RADIUS_R)]
    a = ll_to_xyz(lat1, lng1)
    b = ll_to_xyz(lat2, lng2)
    peak = path_arch_peak(angular_distance_deg(lat1, lng1, lat2, lng2))
    out: list[np.ndarray] = []
    for i in range(n):
        t = i / (n - 1)
        direction = slerp(a, b, t)
        direction = direction / np.linalg.norm(direction)
        radius = PATH_RADIUS_R + peak * math.sin(math.pi * t)
        out.append(direction * radius)
    return out


def _camera_basis(lat: float, lng: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    forward = ll_to_xyz(lat, lng)
    forward = forward / np.linalg.norm(forward)
    up = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    east = np.cross(up, forward)
    en = np.linalg.norm(east)
    if en < 1e-8:
        up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        east = np.cross(up, forward)
        en = np.linalg.norm(east)
    east /= en
    north = np.cross(forward, east)
    north /= np.linalg.norm(north)
    return east, north, forward


def apply_camera_pitch(lat: float, lng: float, pitch_deg: float = PITCH_OFFSET_DEG) -> tuple[float, float]:
    """Shift look-at north of ``(lat, lng)`` for a slight orbital tilt."""
    if abs(pitch_deg) < 1e-9:
        return lat, lng
    _east, north, forward = _camera_basis(lat, lng)
    angle = math.radians(pitch_deg)
    tilted = math.cos(angle) * forward + math.sin(angle) * north
    tilted /= np.linalg.norm(tilted)
    return xyz_to_ll(tilted)


# ---- OSM tiles -------------------------------------------------------------


def tile_cache_dir() -> Path:
    return Path.home() / ".cache" / "syndicator" / "tiles"


def latlng_to_tile(lat: float, lng: float, zoom: int) -> tuple[int, int]:
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 2**zoom
    x = int((lng + 180.0) / 360.0 * n) % n
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    y = max(0, min(n - 1, y))
    return x, y


def lat_to_mercator_y_norm(lat: float) -> float:
    """Latitude degrees → Web Mercator Y in [0, 1] (0 = north)."""
    lat = max(-85.05112878, min(85.05112878, lat))
    lat_r = math.radians(lat)
    return (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0


def mercator_y_norm_to_lat(y_norm: float) -> float:
    """Web Mercator Y in [0, 1] (0 = north) → latitude degrees."""
    y_norm = min(1.0, max(0.0, y_norm))
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y_norm))))


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Return (lat_north, lng_west, lat_south, lng_east) for a tile."""
    n = 2**z

    def lng(tx: int) -> float:
        return tx / n * 360.0 - 180.0

    return (
        mercator_y_norm_to_lat(y / n),
        lng(x),
        mercator_y_norm_to_lat((y + 1) / n),
        lng(x + 1),
    )


def view_half_angle_deg(distance: float, vfov_deg: float = CAMERA_VFOV_DEG) -> float:
    """Approximate angular half-width of the camera footprint on the sphere."""
    altitude = max(0.001, distance - EARTH_RADIUS)
    half = math.degrees(math.atan(math.tan(math.radians(vfov_deg / 2.0)) * altitude))
    limb = math.degrees(math.asin(min(1.0, EARTH_RADIUS / max(distance, 1.001))))
    return max(1.5, min(half * 1.2, limb))


def osm_zoom_for_distance(distance: float) -> int:
    """Map camera distance / footprint to an OSM zoom level."""
    half = view_half_angle_deg(distance)
    # Aim for roughly 8 tiles across the FOV.
    across = max(4.0, math.sqrt(MAX_TILES))
    tile_deg = max(1e-6, (2.0 * half) / across)
    z = int(math.floor(math.log2(360.0 / tile_deg)))
    return max(TILE_ZOOM_MIN, min(TILE_ZOOM_MAX, z))


def visible_tiles(
    focus_lat: float,
    focus_lng: float,
    distance: float,
    *,
    max_tiles: int = MAX_TILES,
) -> list[tuple[int, int, int]]:
    """List ``(z, x, y)`` OSM tiles covering a footprint around focus (legacy helper)."""
    if distance >= DETAIL_DIST_MAX:
        return []
    half = view_half_angle_deg(distance) * 1.6
    z = osm_zoom_for_distance(distance)
    while z >= TILE_ZOOM_MIN:
        lat_min = max(-85.0, focus_lat - half)
        lat_max = min(85.0, focus_lat + half)
        lng_pad = half / max(0.2, math.cos(math.radians(focus_lat)))
        lng_min = focus_lng - lng_pad
        lng_max = focus_lng + lng_pad
        x0, y0 = latlng_to_tile(lat_max, lng_min, z)
        x1, y1 = latlng_to_tile(lat_min, lng_max, z)
        n = 2**z
        if abs(x1 - x0) > n // 2:
            xs = list(range(x0, n)) + list(range(0, x1 + 1))
        else:
            if x1 < x0:
                x0, x1 = x1, x0
            xs = list(range(x0, x1 + 1))
        ys = list(range(min(y0, y1), max(y0, y1) + 1))
        count = len(xs) * len(ys)
        if count <= max_tiles or z == TILE_ZOOM_MIN:
            return [(z, x % n, y) for y in ys for x in xs]
        z -= 1
    return []


def tiles_for_journey(
    positions: list[Position],
    *,
    max_tiles: int = MAX_TILES,
) -> list[tuple[int, int, int]]:
    """Fixed tile set covering the whole journey — stable for the entire close-up."""
    if not positions:
        return []
    lat_min, lat_max, lng_min, lng_max = journey_bbox_deg(positions)

    span = max(lat_max - lat_min, lng_max - lng_min, 0.5)
    # Choose z so ~8 tiles span the larger side.
    across = max(4.0, math.sqrt(max_tiles))
    tile_deg = span / across
    z = int(math.floor(math.log2(360.0 / max(tile_deg, 1e-6))))
    z = max(TILE_ZOOM_MIN, min(TILE_ZOOM_MAX, z))

    while z >= TILE_ZOOM_MIN:
        x0, y0 = latlng_to_tile(lat_max, lng_min, z)
        x1, y1 = latlng_to_tile(lat_min, lng_max, z)
        n = 2**z
        if abs(x1 - x0) > n // 2:
            xs = list(range(x0, n)) + list(range(0, x1 + 1))
        else:
            if x1 < x0:
                x0, x1 = x1, x0
            xs = list(range(x0, x1 + 1))
        ys = list(range(min(y0, y1), max(y0, y1) + 1))
        count = len(xs) * len(ys)
        if count <= max_tiles or z == TILE_ZOOM_MIN:
            return [(z, x % n, y) for y in ys for x in xs]
        z -= 1
    return []


def default_tile_fetcher(cache_dir: Path | None = None) -> TileFetcher:
    """HTTP OSM tile fetcher with on-disk cache."""
    root = cache_dir or tile_cache_dir()
    client = httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=30.0)

    def fetch(z: int, x: int, y: int) -> Image.Image:
        path = root / str(z) / str(x) / f"{y}.png"
        if path.exists():
            with path.open("rb") as f:
                return Image.open(f).convert("RGB")
        path.parent.mkdir(parents=True, exist_ok=True)
        url = _OSM_TILE_URL.format(z=z, x=x, y=y)
        resp = client.get(url)
        resp.raise_for_status()
        path.write_bytes(resp.content)
        return Image.open(BytesIO(resp.content)).convert("RGB")

    fetch.close = client.close  # type: ignore[attr-defined]
    return fetch


def solid_tile_fetcher(color: tuple[int, int, int] = (200, 210, 220)) -> TileFetcher:
    """Offline stub that returns a solid-color 256×256 tile."""

    def fetch(z: int, x: int, y: int) -> Image.Image:
        return Image.new("RGB", (256, 256), color)

    return fetch


# ---- PyVista scene ---------------------------------------------------------


def _equirect_sphere(texture_img: Image.Image, resolution: int = 90) -> tuple[pv.PolyData, pv.Texture]:
    """Unit sphere with equirectangular UVs and the given texture."""
    sphere = pv.Sphere(
        radius=EARTH_RADIUS,
        theta_resolution=resolution,
        phi_resolution=resolution,
    )
    pts = sphere.points
    lng = np.arctan2(pts[:, 1], pts[:, 0])
    lat = np.arcsin(np.clip(pts[:, 2] / EARTH_RADIUS, -1.0, 1.0))
    u = (lng + np.pi) / (2 * np.pi)
    # OpenGL/VTK: V=1 samples the first image row (north in equirect sources).
    v = 0.5 + lat / np.pi
    sphere.active_texture_coordinates = np.column_stack([u, np.clip(v, 0.0, 1.0)])
    arr = np.asarray(texture_img.convert("RGB"), dtype=np.uint8)
    tex = pv.Texture(arr)
    return sphere, tex


def _mosaic_from_tiles(
    tiles: list[tuple[int, int, int]],
    fetcher: TileFetcher,
) -> tuple[Image.Image, int, int, int, int, int] | None:
    """Composite OSM tiles; return (img, z, x0, x1, y0, y1) inclusive tile indices."""
    if not tiles:
        return None
    z = tiles[0][0]
    xs = sorted({t[1] for t in tiles})
    ys = sorted({t[2] for t in tiles})
    # Refuse pathological antimeridian wraps for the mosaic.
    if max(xs) - min(xs) + 1 != len(xs):
        return None
    tile_w = tile_h = 256
    mosaic = Image.new("RGB", (len(xs) * tile_w, len(ys) * tile_h))
    x0, x1 = xs[0], xs[-1]
    y0, y1 = ys[0], ys[-1]
    for z_t, x, y in tiles:
        if z_t != z:
            continue
        img = fetcher(z, x, y)
        px = (x - x0) * tile_w
        py = (y - y0) * tile_h
        mosaic.paste(img.convert("RGB"), (px, py))
    return mosaic, z, x0, x1, y0, y1


def _region_patch_mesh(
    img: Image.Image,
    z: int,
    x0: int,
    x1: int,
    y0: int,
    y1: int,
    subdivisions: int = 48,
) -> tuple[pv.PolyData, pv.Texture]:
    """Textured patch for an OSM mosaic; vertices follow Web Mercator, not linear lat.

    OSM tiles are Mercator: equal image rows ≠ equal latitude. Using linear lat
    shifts features north/south by tens of km at mid-latitudes.
    """
    n = 2**z
    lng_w = x0 / n * 360.0 - 180.0
    lng_e = (x1 + 1) / n * 360.0 - 180.0
    if lng_e < lng_w:
        lng_e += 360.0
    y_north = y0 / n
    y_south = (y1 + 1) / n

    nu = subdivisions + 1
    nv = subdivisions + 1
    lngs = np.linspace(lng_w, lng_e, nu)
    points = []
    uvs = []
    for iv in range(nv):
        t = iv / (nv - 1)  # 0 at image top / geographic north of mosaic
        y_norm = y_north + t * (y_south - y_north)
        lat = mercator_y_norm_to_lat(float(y_norm))
        for iu, lng in enumerate(lngs):
            wrapped = ((lng + 180) % 360) - 180
            points.append(ll_to_xyz(lat, wrapped, TILE_RADIUS))
            # Image row 0 is north; VTK V=1 samples first row.
            uvs.append([iu / (nu - 1), 1.0 - t])
    points_a = np.asarray(points, dtype=np.float64)
    uvs_a = np.asarray(uvs, dtype=np.float64)
    faces = []
    for iv in range(subdivisions):
        for iu in range(subdivisions):
            i0 = iv * nu + iu
            i1 = i0 + 1
            i2 = i0 + nu + 1
            i3 = i0 + nu
            faces.extend([3, i0, i1, i2])
            faces.extend([3, i0, i2, i3])
    mesh = pv.PolyData(points_a, faces=np.asarray(faces, dtype=np.int64))
    mesh.active_texture_coordinates = uvs_a
    mesh.compute_normals(cell_normals=False, point_normals=True, inplace=True)
    tex = pv.Texture(np.asarray(img.convert("RGB"), dtype=np.uint8))
    return mesh, tex


def _path_polydata(points: list[np.ndarray]) -> pv.PolyData | None:
    if len(points) < 2:
        return None
    xyz = np.asarray(points, dtype=np.float64)
    return pv.lines_from_points(xyz)


def _camera_pose(
    focus_lat: float,
    focus_lng: float,
    distance: float,
    *,
    pitch: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (position, focal_point, view_up) for PyVista.

    ``pitch`` is 0..1, blending toward a mild northward look-at offset.
    """
    pitch = min(1.0, max(0.0, pitch))
    if pitch > 1e-6:
        max_pitch = min(PITCH_OFFSET_DEG, view_half_angle_deg(distance) * 0.35)
        look_lat, look_lng = apply_camera_pitch(focus_lat, focus_lng, max_pitch * pitch)
    else:
        look_lat, look_lng = focus_lat, focus_lng
    focal = ll_to_xyz(look_lat, look_lng, EARTH_RADIUS)
    direction = focal / np.linalg.norm(focal)
    position = direction * distance
    _east, north, _fwd = _camera_basis(look_lat, look_lng)
    view_up = north
    return position, focal, view_up


def world_to_pixel(
    plotter: pv.Plotter,
    xyz: np.ndarray,
    img_w: int,
    img_h: int,
) -> tuple[float, float, bool]:
    """Project a world point to screenshot pixel coords; visible if on-screen and in front."""
    ren = plotter.renderer
    ren.SetWorldPoint(float(xyz[0]), float(xyz[1]), float(xyz[2]), 1.0)
    ren.WorldToDisplay()
    dx, dy, dz = ren.GetDisplayPoint()
    rw, rh = ren.GetSize()
    # Normalize in case display size differs from requested screenshot size.
    if rw > 0 and rh > 0:
        px = dx * img_w / rw
        py = (rh - dy) * img_h / rh
    else:
        px, py = dx, img_h - dy
    # dz in (0, 1) after projection; near 0 = closer to camera in some VTK setups,
    # but WorldToDisplay returns display z where smaller is nearer. Require on-screen.
    visible = 0.0 <= px <= img_w and 0.0 <= py <= img_h and 0.0 <= dz <= 1.0
    return px, py, visible


@dataclass
class GlobeRenderer:
    """Owns a reusable offscreen PyVista plotter for frame rendering."""

    earth_texture: Image.Image
    tile_fetcher: TileFetcher
    img_w: int = IMG_WIDTH
    img_h: int = IMG_HEIGHT

    def __post_init__(self) -> None:
        pv.OFF_SCREEN = True
        self._plotter = pv.Plotter(
            off_screen=True,
            window_size=(self.img_w, self.img_h),
        )
        self._plotter.set_background("white")
        self._sphere, self._base_tex = _equirect_sphere(self.earth_texture)
        self._sphere_actor = self._plotter.add_mesh(
            self._sphere,
            texture=self._base_tex,
            smooth_shading=True,
            show_edges=False,
        )
        self._detail_actor = None
        self._detail_ready = False
        self._detail_visible = False
        self._path_actor = None
        self._last_path_len = -1
        # Disable default lighting extremes for a flatter map look.
        self._plotter.remove_all_lights()
        light = pv.Light(position=(5, 5, 5), light_type="scene light")
        light.intensity = 0.85
        self._plotter.add_light(light)
        fill = pv.Light(position=(-3, -2, 4), light_type="scene light")
        fill.intensity = 0.35
        self._plotter.add_light(fill)

    def close(self) -> None:
        self._plotter.close()
        close = getattr(self.tile_fetcher, "close", None)
        if callable(close):
            close()

    def prepare_journey_detail(self, positions: list[Position]) -> None:
        """Build one fixed OSM mosaic for the journey (no per-frame rebuilds)."""
        if self._detail_actor is not None:
            self._plotter.remove_actor(self._detail_actor, render=False)
            self._detail_actor = None
        self._detail_ready = False
        self._detail_visible = False
        tiles = tiles_for_journey(positions)
        if not tiles:
            return
        mosaic = _mosaic_from_tiles(tiles, self.tile_fetcher)
        if mosaic is None:
            return
        img, z, x0, x1, y0, y1 = mosaic
        mesh, tex = _region_patch_mesh(img, z, x0, x1, y0, y1)
        self._detail_actor = self._plotter.add_mesh(
            mesh,
            texture=tex,
            smooth_shading=True,
            show_edges=False,
        )
        self._detail_actor.SetVisibility(False)
        self._detail_ready = True

    def _set_detail_visible(self, visible: bool) -> None:
        if not self._detail_ready or self._detail_actor is None:
            self._sphere_actor.SetVisibility(True)
            return
        if visible == self._detail_visible:
            return
        # Never mix globe + mosaic: exactly one of them is shown.
        self._detail_actor.SetVisibility(visible)
        self._sphere_actor.SetVisibility(not visible)
        self._detail_visible = visible

    def _set_path(self, points: list[np.ndarray]) -> None:
        # Skip rebuild when the stroked path hasn't grown (avoids per-hold flicker).
        if len(points) == self._last_path_len and self._path_actor is not None:
            return
        if self._path_actor is not None:
            self._plotter.remove_actor(self._path_actor, render=False)
            self._path_actor = None
        poly = _path_polydata(points)
        self._last_path_len = len(points)
        if poly is None:
            return
        tube = poly.tube(radius=PATH_RADIUS, n_sides=12)
        self._path_actor = self._plotter.add_mesh(
            tube,
            color=PATH_COLOR,
            smooth_shading=True,
        )

    def render_frame(
        self,
        *,
        focus_lat: float,
        focus_lng: float,
        distance: float,
        path_points: list[np.ndarray],
        pitch: float,
        use_detail: bool,
    ) -> Image.Image:
        pos, focal, view_up = _camera_pose(
            focus_lat, focus_lng, distance, pitch=pitch
        )
        self._plotter.camera_position = [
            tuple(pos.tolist()),
            tuple(focal.tolist()),
            tuple(view_up.tolist()),
        ]
        self._plotter.camera.view_angle = CAMERA_VFOV_DEG

        self._set_detail_visible(use_detail and distance <= DETAIL_DIST_MAX)
        self._set_path(path_points)

        self._plotter.render()
        shot = self._plotter.screenshot(return_img=True, transparent_background=False)
        return Image.fromarray(shot).convert("RGBA")

    def project_ll(
        self,
        lat: float,
        lng: float,
    ) -> tuple[float, float, bool]:
        return world_to_pixel(
            self._plotter,
            ll_to_xyz(lat, lng, PATH_RADIUS_R),
            self.img_w,
            self.img_h,
        )

    def project_xyz(self, xyz: np.ndarray) -> tuple[float, float, bool]:
        return world_to_pixel(self._plotter, xyz, self.img_w, self.img_h)


# ---- timeline --------------------------------------------------------------


@dataclass(frozen=True)
class _FrameState:
    focus_lat: float
    focus_lng: float
    distance: float
    path_points: list[np.ndarray]
    marker_indices: list[int]
    traveler: np.ndarray | None
    pitch: float
    use_detail: bool


def journey_center(positions: list[Position]) -> tuple[float, float]:
    """Geographic centre used to frame the full route on zoom-out."""
    if not positions:
        return 0.0, 0.0
    if len(positions) == 1:
        return positions[0].lat, positions[0].lng
    # Average unit vectors to avoid antimeridian / pole issues.
    acc = np.zeros(3, dtype=np.float64)
    for p in positions:
        acc += ll_to_xyz(p.lat, p.lng)
    acc /= np.linalg.norm(acc)
    return xyz_to_ll(acc)


def build_frame_states(positions: list[Position]) -> list[_FrameState]:
    """Expand journey into per-frame camera / path / marker state."""
    if not positions:
        return []

    close_dist = close_camera_distance(positions)
    overview_dist = overview_camera_distance(positions)
    center_lat, center_lng = journey_center(positions)
    states: list[_FrameState] = []
    completed_path: list[np.ndarray] = [
        ll_to_xyz(positions[0].lat, positions[0].lng, PATH_RADIUS_R)
    ]
    p0_lat, p0_lng = positions[0].lat, positions[0].lng

    for _ in range(INTRO_HOLD):
        states.append(
            _FrameState(
                focus_lat=p0_lat,
                focus_lng=p0_lng,
                distance=CAM_DIST_WIDE,
                path_points=list(completed_path),
                marker_indices=[0],
                traveler=None,
                pitch=0.0,
                use_detail=False,
            )
        )

    for f in range(ZOOM_IN_FRAMES):
        t = _ease_in_out(1.0 if ZOOM_IN_FRAMES <= 1 else (f + 1) / ZOOM_IN_FRAMES)
        dist = CAM_DIST_WIDE + t * (close_dist - CAM_DIST_WIDE)
        states.append(
            _FrameState(
                focus_lat=p0_lat,
                focus_lng=p0_lng,
                distance=dist,
                path_points=list(completed_path),
                marker_indices=[0],
                traveler=None,
                pitch=t,
                # Only show detail once fully zoomed — avoids mid-zoom mosaic pops.
                use_detail=dist <= DETAIL_DIST_MAX and t > 0.92,
            )
        )

    for i in range(len(positions) - 1):
        a, b = positions[i], positions[i + 1]
        dist_deg = angular_distance_deg(a.lat, a.lng, b.lat, b.lng)
        n_travel = travel_frames_for_distance(dist_deg)
        leg = great_circle_arch_xyz(a.lat, a.lng, b.lat, b.lng, PATH_SAMPLES)
        a_xyz = ll_to_xyz(a.lat, a.lng)
        b_xyz = ll_to_xyz(b.lat, b.lng)

        for f in range(n_travel):
            # Linear progress — easing per leg caused visible slowdown/jump at waypoints.
            t = 1.0 if n_travel <= 1 else (f + 1) / n_travel
            trav_lat, trav_lng = xyz_to_ll(slerp(a_xyz, b_xyz, t))
            end_idx = max(1, int(round(t * (len(leg) - 1))))
            progressive = completed_path + leg[1 : end_idx + 1]
            states.append(
                _FrameState(
                    focus_lat=trav_lat,
                    focus_lng=trav_lng,
                    distance=close_dist,
                    path_points=progressive,
                    marker_indices=list(range(i + 1)),
                    traveler=leg[end_idx],
                    pitch=1.0,
                    use_detail=True,
                )
            )

        completed_path = completed_path + leg[1:]
        hold_n = hold_frames_for_days(b.days)
        for _ in range(hold_n):
            states.append(
                _FrameState(
                    focus_lat=b.lat,
                    focus_lng=b.lng,
                    distance=close_dist,
                    path_points=list(completed_path),
                    marker_indices=list(range(i + 2)),
                    traveler=None,
                    pitch=1.0,
                    use_detail=True,
                )
            )

    last = positions[-1]
    last_xyz = ll_to_xyz(last.lat, last.lng)
    center_xyz = ll_to_xyz(center_lat, center_lng)

    # Pull back only far enough to frame the full route on the tile mosaic.
    for f in range(ZOOM_OUT_FRAMES):
        t = _ease_in_out(1.0 if ZOOM_OUT_FRAMES <= 1 else (f + 1) / ZOOM_OUT_FRAMES)
        dist = close_dist + t * (overview_dist - close_dist)
        focus_lat, focus_lng = xyz_to_ll(slerp(last_xyz, center_xyz, t))
        states.append(
            _FrameState(
                focus_lat=focus_lat,
                focus_lng=focus_lng,
                distance=dist,
                path_points=list(completed_path),
                marker_indices=list(range(len(positions))),
                traveler=None,
                pitch=1.0 - 0.25 * t,
                use_detail=True,
            )
        )

    for _ in range(OUTRO_HOLD):
        states.append(
            _FrameState(
                focus_lat=center_lat,
                focus_lng=center_lng,
                distance=overview_dist,
                path_points=list(completed_path),
                marker_indices=list(range(len(positions))),
                traveler=None,
                pitch=0.75,
                use_detail=True,
            )
        )
    return states


def total_frames(positions: list[Position]) -> int:
    return len(build_frame_states(positions)) if positions else OUTRO_HOLD


def scale_image(src: Image.Image, size: int) -> Image.Image:
    return src.resize((size, size), Image.Resampling.BICUBIC)


def draw_marker(frame: Image.Image, scaled: Image.Image, px: float, py: float) -> None:
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


def generate_animation(
    journey: JourneyMap,
    output_path: Path | str,
    *,
    earth_texture: Image.Image | None = None,
    logo: Image.Image | None = None,
    tile_fetcher: TileFetcher | None = None,
) -> None:
    """Render ``journey`` to an H.264 MP4 at ``output_path``.

    ``earth_texture``, ``logo``, and ``tile_fetcher`` may be injected for tests.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on $PATH. Install it with: brew install ffmpeg")

    output_path = Path(output_path)
    if not journey.positions:
        log.info("No positions found, skipping animation")
        return

    if logo is None:
        logo = load_logo()
    if earth_texture is None:
        log.info("Loading Earth texture...")
        earth_texture = load_earth_texture()
    own_fetcher = tile_fetcher is None
    if tile_fetcher is None:
        tile_fetcher = default_tile_fetcher()

    states = build_frame_states(journey.positions)
    total = len(states)
    scaled_cache: dict[int, Image.Image] = {}

    def cached_scale(size: int) -> Image.Image:
        if size not in scaled_cache:
            scaled_cache[size] = scale_image(logo, size)
        return scaled_cache[size]

    renderer = GlobeRenderer(
        earth_texture=earth_texture,
        tile_fetcher=tile_fetcher,
        img_w=IMG_WIDTH * RENDER_SCALE,
        img_h=IMG_HEIGHT * RENDER_SCALE,
    )
    try:
        log.info("Preparing journey map tiles...")
        renderer.prepare_journey_detail(journey.positions)
        with tempfile.TemporaryDirectory(prefix="animatemap_") as tmp:
            tmp_dir = Path(tmp)
            for fi, st in enumerate(states):
                if fi % 30 == 0:
                    log.info("  frame %d / %d", fi, total)
                frame = renderer.render_frame(
                    focus_lat=st.focus_lat,
                    focus_lng=st.focus_lng,
                    distance=st.distance,
                    path_points=st.path_points,
                    pitch=st.pitch,
                    use_detail=st.use_detail,
                )
                # Marker size tracks the on-screen path thickness.
                size = route_marker_size(st.distance, render_scale=RENDER_SCALE)
                for mi in st.marker_indices:
                    p = journey.positions[mi]
                    px, py, vis = renderer.project_ll(p.lat, p.lng)
                    if vis:
                        draw_marker(frame, cached_scale(size), px, py)
                if st.traveler is not None:
                    px, py, vis = renderer.project_xyz(st.traveler)
                    if vis:
                        draw_marker(frame, cached_scale(size), px, py)
                # Supersample downsample → sharper OSM labels than native 900×500.
                if RENDER_SCALE != 1:
                    frame = frame.resize((IMG_WIDTH, IMG_HEIGHT), Image.Resampling.LANCZOS)
                frame.convert("RGB").save(tmp_dir / f"frame_{fi:04d}.png")

            log.info("Assembling %d frames into %s...", total, output_path)
            _run_ffmpeg(tmp_dir, output_path)
    finally:
        renderer.close()
        if own_fetcher:
            close = getattr(tile_fetcher, "close", None)
            if callable(close):
                close()


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
        "-preset",
        "slow",
        "-crf",
        str(FFMPEG_CRF),
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr}")
