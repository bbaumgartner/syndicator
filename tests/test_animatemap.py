"""Tests for 3D journey globe animation (PyVista + OSM tiles)."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pytest
from PIL import Image

from syndicator.animatemap import (
    CAM_DIST_CLOSE_MAX,
    CAM_DIST_CLOSE_MIN,
    CAM_DIST_WIDE,
    DETAIL_DIST_MAX,
    IMG_HEIGHT,
    IMG_WIDTH,
    INTRO_HOLD,
    MAX_HOLD_FRAMES,
    MIN_HOLD_FRAMES,
    OUTRO_HOLD,
    TILE_ZOOM_MIN,
    ZOOM_IN_FRAMES,
    ZOOM_OUT_FRAMES,
    angular_distance_deg,
    build_frame_states,
    close_camera_distance,
    generate_animation,
    great_circle_points,
    hold_frames_for_days,
    journey_center,
    latlng_to_tile,
    ll_to_xyz,
    load_earth_texture,
    load_logo,
    marker_size,
    osm_zoom_for_distance,
    scale_image,
    slerp,
    solid_tile_fetcher,
    tile_bounds,
    tiles_for_journey,
    total_frames,
    view_half_angle_deg,
    visible_tiles,
    xyz_to_ll,
    _linear_interp,
)
from syndicator.journeymap import JourneyMap, Position, write_journey_json


# ---- sphere math ------------------------------------------------------------


def test_ll_to_xyz_equator():
    v = ll_to_xyz(0.0, 0.0)
    assert abs(np_norm(v) - 1.0) < 1e-9
    assert abs(v[0] - 1.0) < 1e-9


def np_norm(v):
    return math.sqrt(float(v[0] ** 2 + v[1] ** 2 + v[2] ** 2))


def test_ll_xyz_roundtrip():
    for lat, lng in ((0.0, 0.0), (45.0, 13.0), (-30.0, 120.0), (80.0, -170.0)):
        got_lat, got_lng = xyz_to_ll(ll_to_xyz(lat, lng))
        assert abs(got_lat - lat) < 1e-6
        assert abs(((got_lng - lng + 180) % 360) - 180) < 1e-6


def test_slerp_endpoints():
    import numpy as np

    a = ll_to_xyz(0.0, 0.0)
    b = ll_to_xyz(0.0, 90.0)
    assert np.allclose(slerp(a, b, 0.0), a, atol=1e-9)
    assert np.allclose(slerp(a, b, 1.0), b, atol=1e-9)


def test_slerp_midpoint_unit():
    import numpy as np

    a = ll_to_xyz(0.0, 0.0)
    b = ll_to_xyz(0.0, 90.0)
    mid = slerp(a, b, 0.5)
    assert abs(np.linalg.norm(mid) - 1.0) < 1e-9
    lat, lng = xyz_to_ll(mid)
    assert abs(lat) < 1e-6
    assert abs(lng - 45.0) < 1e-4


def test_great_circle_midpoint_roughly_halfway():
    pts = great_circle_points(0.0, 0.0, 0.0, 90.0, 5)
    assert len(pts) == 5
    assert abs(pts[0][0]) < 1e-6 and abs(pts[0][1]) < 1e-6
    assert abs(pts[-1][1] - 90.0) < 1e-4
    assert abs(pts[2][1] - 45.0) < 1.0


def test_angular_distance_quarter():
    assert abs(angular_distance_deg(0.0, 0.0, 0.0, 90.0) - 90.0) < 1e-6


# ---- tiles ------------------------------------------------------------------


def test_latlng_to_tile_known():
    # Equator / prime meridian area at z=1 → tile near (1, 1) or (0, 0) depending on scheme.
    x, y = latlng_to_tile(0.0, 0.0, 1)
    assert x in (0, 1)
    assert y in (0, 1)


def test_tile_bounds_ordering():
    lat_n, lng_w, lat_s, lng_e = tile_bounds(3, 4, 2)
    assert lat_n > lat_s
    assert lng_e > lng_w


def test_visible_tiles_empty_when_far():
    assert visible_tiles(45.0, 13.0, CAM_DIST_WIDE) == []
    assert visible_tiles(45.0, 13.0, DETAIL_DIST_MAX) == []


def test_visible_tiles_near_are_capped():
    tiles = visible_tiles(44.5, 15.0, CAM_DIST_CLOSE_MIN)
    assert tiles
    assert len(tiles) <= 64
    zs = {t[0] for t in tiles}
    assert len(zs) == 1
    assert TILE_ZOOM_MIN <= next(iter(zs)) <= 13


def test_tiles_for_journey_stable_and_covers_stops():
    positions = [
        Position(date="a", lat=45.5127, lng=13.5954, days=1),
        Position(date="b", lat=43.5088, lng=16.4402, days=1),
    ]
    a = tiles_for_journey(positions)
    b = tiles_for_journey(positions)
    assert a == b
    assert a
    assert len(a) <= 64
    # Single zoom level for the whole journey mosaic.
    assert len({t[0] for t in a}) == 1


def test_camera_focus_continuous_across_legs():
    positions = [
        Position(date="a", lat=45.0, lng=13.0, days=1),
        Position(date="b", lat=44.0, lng=14.0, days=1),
        Position(date="c", lat=43.0, lng=15.0, days=1),
    ]
    states = build_frame_states(positions)
    travel = [s for s in states if s.use_detail and s.traveler is not None]
    assert len(travel) >= 4
    # Consecutive traveler positions should move a small angular step (no teleport).
    for prev, cur in zip(travel, travel[1:]):
        step = angular_distance_deg(
            prev.traveler[0], prev.traveler[1], cur.traveler[0], cur.traveler[1]
        )
        # Allow a larger step only when starting a new leg (waypoint).
        assert step < 5.0


def test_pitch_is_continuous_on_zoom():
    positions = [Position(date="a", lat=45.0, lng=13.0, days=1)]
    states = build_frame_states(positions)
    zoom = states[INTRO_HOLD : INTRO_HOLD + ZOOM_IN_FRAMES]
    pitches = [s.pitch for s in zoom]
    assert pitches[0] < pitches[-1]
    assert all(0.0 <= p <= 1.0 for p in pitches)
    # No abrupt 0↔1 flip in a single frame beyond a smooth step.
    for a, b in zip(pitches, pitches[1:]):
        assert abs(b - a) < 0.25


def test_osm_zoom_closer_is_higher():
    assert osm_zoom_for_distance(CAM_DIST_CLOSE_MIN) >= osm_zoom_for_distance(1.5)


def test_view_half_angle_positive():
    assert view_half_angle_deg(CAM_DIST_WIDE) > 0
    assert view_half_angle_deg(CAM_DIST_CLOSE_MIN) > 0
    assert view_half_angle_deg(CAM_DIST_CLOSE_MIN) < view_half_angle_deg(CAM_DIST_WIDE)


# ---- camera / timeline ------------------------------------------------------


def test_close_camera_distance_tighter_for_short_span():
    short = [
        Position(date="a", lat=45.5, lng=13.6, days=1),
        Position(date="b", lat=43.5, lng=16.4, days=1),
    ]
    long = [
        Position(date="a", lat=40.0, lng=-74.0, days=1),
        Position(date="b", lat=40.0, lng=0.0, days=1),
    ]
    assert close_camera_distance(short) < close_camera_distance(long)
    assert CAM_DIST_CLOSE_MIN <= close_camera_distance(short) <= CAM_DIST_CLOSE_MAX


def test_build_frame_states_single():
    positions = [Position(date="a", lat=45.0, lng=13.0, days=5)]
    states = build_frame_states(positions)
    assert len(states) == INTRO_HOLD + ZOOM_IN_FRAMES + ZOOM_OUT_FRAMES + OUTRO_HOLD
    assert states[0].marker_indices == [0]
    assert states[0].traveler is None
    assert states[0].distance == CAM_DIST_WIDE
    assert states[-1].distance == CAM_DIST_WIDE


def test_build_frame_states_zooms_from_wide_to_close():
    positions = [
        Position(date="a", lat=45.5, lng=13.6, days=1),
        Position(date="b", lat=43.5, lng=16.4, days=1),
    ]
    states = build_frame_states(positions)
    assert states[0].distance == CAM_DIST_WIDE
    zoom_end = states[INTRO_HOLD + ZOOM_IN_FRAMES - 1]
    assert zoom_end.distance == pytest.approx(close_camera_distance(positions))
    # Mid-journey (after zoom-in, before zoom-out) stays close.
    mid = states[INTRO_HOLD + ZOOM_IN_FRAMES]
    assert mid.distance == pytest.approx(close_camera_distance(positions))
    assert states[-1].distance == CAM_DIST_WIDE


def test_build_frame_states_outro_shows_full_route():
    positions = [
        Position(date="a", lat=45.5, lng=13.6, days=1),
        Position(date="b", lat=43.5, lng=16.4, days=1),
    ]
    states = build_frame_states(positions)
    assert states[-1].distance == CAM_DIST_WIDE
    assert states[-1].marker_indices == [0, 1]
    assert len(states[-1].path_points) >= 2
    # Zoom-out segment increases distance toward wide.
    outro_start = len(states) - OUTRO_HOLD - ZOOM_OUT_FRAMES
    dists = [s.distance for s in states[outro_start : outro_start + ZOOM_OUT_FRAMES]]
    assert dists[0] < dists[-1]
    assert dists[-1] == pytest.approx(CAM_DIST_WIDE)


def test_journey_center_midpoint():
    positions = [
        Position(date="a", lat=40.0, lng=10.0, days=1),
        Position(date="b", lat=50.0, lng=20.0, days=1),
    ]
    lat, lng = journey_center(positions)
    assert 40.0 < lat < 50.0
    assert 10.0 < lng < 20.0


def test_build_frame_states_two_has_travel():
    positions = [
        Position(date="a", lat=45.0, lng=13.0, days=5),
        Position(date="b", lat=43.5, lng=16.4, days=3),
    ]
    states = build_frame_states(positions)
    assert any(s.traveler is not None for s in states)
    assert states[-1].marker_indices == [0, 1]


def test_total_frames_matches_states():
    positions = [
        Position(date="a", lat=45.0, lng=13.0, days=1),
        Position(date="b", lat=44.0, lng=14.0, days=1),
    ]
    assert total_frames(positions) == len(build_frame_states(positions))
    assert total_frames([]) == OUTRO_HOLD


# ---- linear_interp / marker / hold ------------------------------------------


def test_linear_interp_endpoints():
    assert _linear_interp(1, 10, 50) == 10
    assert _linear_interp(30, 10, 50) == 50


def test_marker_size_endpoints():
    assert marker_size(1) == 30
    assert marker_size(30) == 100


def test_hold_frames_endpoints():
    assert hold_frames_for_days(1) == MIN_HOLD_FRAMES
    assert hold_frames_for_days(30) == MAX_HOLD_FRAMES
    assert MAX_HOLD_FRAMES <= 8  # keeps stops brief / smooth


# ---- assets / scale ---------------------------------------------------------


def test_scale_image_output_size():
    src = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    for size in (30, 50, 80, 100):
        assert scale_image(src, size).size == (size, size)


def test_load_logo():
    logo = load_logo()
    assert logo.mode == "RGBA"
    assert logo.size == (400, 400)


def test_load_earth_texture():
    earth = load_earth_texture()
    assert earth.mode == "RGB"
    w, h = earth.size
    assert w == 2 * h


def test_solid_tile_fetcher():
    fetcher = solid_tile_fetcher((10, 20, 30))
    img = fetcher(5, 1, 2)
    assert img.size == (256, 256)
    assert img.getpixel((0, 0)) == (10, 20, 30)


# ---- generate_animation -----------------------------------------------------


def test_generate_animation_requires_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("syndicator.animatemap.shutil.which", lambda _: None)
    journey = JourneyMap(positions=[Position(date="2025-09-13", lat=45.5, lng=13.6, days=5)])
    with pytest.raises(RuntimeError, match="ffmpeg"):
        generate_animation(journey, tmp_path / "out.mp4")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_generate_animation_produces_file(tmp_path: Path):
    pytest.importorskip("pyvista")
    journey = JourneyMap(
        positions=[
            Position(date="2025-09-13", lat=45.5127, lng=13.5954, days=2),
            Position(date="2026-01-17", lat=43.5088, lng=16.4402, days=2),
        ]
    )
    output = tmp_path / "journey.mp4"
    earth = Image.new("RGB", (64, 32), (30, 90, 160))
    logo = Image.new("RGBA", (40, 40), (255, 0, 0, 200))
    generate_animation(
        journey,
        output,
        earth_texture=earth,
        logo=logo,
        tile_fetcher=solid_tile_fetcher((180, 200, 160)),
    )
    assert output.exists()
    assert output.stat().st_size > 0


def test_write_and_roundtrip_json(tmp_path: Path):
    path = tmp_path / "journey.json"
    journey = JourneyMap(
        positions=[Position(date="2025-09-13", lat=45.5, lng=13.6, days=10)]
    )
    write_journey_json(journey, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["positions"][0]["lat"] == 45.5


def test_image_constants():
    assert IMG_WIDTH > 0 and IMG_HEIGHT > 0
