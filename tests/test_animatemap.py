"""Tests for in-process journey map animation (ported from Go animatemap)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from syndicator.animatemap import (
    BOUNCE_AMP,
    BOUNCE_FRAMES,
    FINAL_HOLD,
    FLY_IN_FRAMES,
    FLY_IN_OVERLAP,
    IMG_HEIGHT,
    IMG_WIDTH,
    MAX_HOLD_FRAMES,
    MIN_HOLD_FRAMES,
    bounce_multiplier,
    choose_bounds_and_zoom,
    generate_animation,
    hold_frames_for_days,
    lat_lng_to_pixel,
    load_logo,
    marker_size,
    mercator_y,
    position_start_frames,
    scale_image,
    total_frames,
    _linear_interp,
)
from syndicator.journeymap import JourneyMap, Position, write_journey_json


# ---- mercator_y -------------------------------------------------------------


def test_mercator_y_equator():
    assert abs(mercator_y(0) - 0.5) < 1e-9


def test_mercator_y_monotonicity():
    assert mercator_y(45) < mercator_y(0)
    assert mercator_y(-45) > mercator_y(0)


def test_mercator_y_symmetry():
    assert abs(mercator_y(45) + mercator_y(-45) - 1.0) < 1e-9


# ---- lat_lng_to_pixel -------------------------------------------------------


def test_lat_lng_to_pixel_center():
    x, y = lat_lng_to_pixel(45.0, 13.0, 7, 45.0, 13.0, IMG_WIDTH, IMG_HEIGHT)
    assert abs(x - IMG_WIDTH / 2) < 0.5
    assert abs(y - IMG_HEIGHT / 2) < 0.5


def test_lat_lng_to_pixel_east_is_right():
    x1, _ = lat_lng_to_pixel(0, 0, 5, 0, 0, IMG_WIDTH, IMG_HEIGHT)
    x2, _ = lat_lng_to_pixel(0, 10, 5, 0, 0, IMG_WIDTH, IMG_HEIGHT)
    assert x2 > x1


def test_lat_lng_to_pixel_north_is_up():
    _, y1 = lat_lng_to_pixel(0, 0, 5, 0, 0, IMG_WIDTH, IMG_HEIGHT)
    _, y2 = lat_lng_to_pixel(10, 0, 5, 0, 0, IMG_WIDTH, IMG_HEIGHT)
    assert y2 < y1


# ---- choose_bounds_and_zoom -------------------------------------------------


def test_choose_bounds_empty():
    assert choose_bounds_and_zoom([], IMG_WIDTH, IMG_HEIGHT) == (0.0, 0.0, 1)


def test_choose_bounds_single_point():
    lat, lng, zoom = choose_bounds_and_zoom(
        [Position(date="2025-09-13", lat=45.5, lng=13.6, days=10)],
        IMG_WIDTH,
        IMG_HEIGHT,
    )
    assert abs(lat - 45.5) < 1e-9
    assert abs(lng - 13.6) < 1e-9
    assert 1 <= zoom <= 15


def test_choose_bounds_all_fit_with_padding():
    positions = [
        Position(date="a", lat=45.5, lng=13.6, days=10),
        Position(date="b", lat=43.5, lng=16.4, days=5),
        Position(date="c", lat=44.8, lng=14.0, days=3),
    ]
    padding = 80.0
    center_lat, center_lng, zoom = choose_bounds_and_zoom(positions, IMG_WIDTH, IMG_HEIGHT)
    for p in positions:
        x, y = lat_lng_to_pixel(p.lat, p.lng, zoom, center_lat, center_lng, IMG_WIDTH, IMG_HEIGHT)
        assert padding <= x <= IMG_WIDTH - padding
        assert padding <= y <= IMG_HEIGHT - padding


def test_choose_bounds_centre_is_midpoint():
    positions = [
        Position(date="a", lat=40.0, lng=10.0, days=1),
        Position(date="b", lat=50.0, lng=20.0, days=1),
    ]
    lat, lng, _ = choose_bounds_and_zoom(positions, IMG_WIDTH, IMG_HEIGHT)
    assert abs(lat - 45.0) < 1e-9
    assert abs(lng - 15.0) < 1e-9


# ---- linear_interp / marker_size / hold -------------------------------------


def test_linear_interp_endpoints():
    assert _linear_interp(1, 10, 50) == 10
    assert _linear_interp(30, 10, 50) == 50


def test_linear_interp_clamped():
    assert _linear_interp(0, 10, 50) == 10
    assert _linear_interp(-5, 10, 50) == 10
    assert _linear_interp(100, 10, 50) == 50


def test_linear_interp_midpoint():
    assert 40 <= _linear_interp(15, 0, 100) <= 60


def test_linear_interp_equal_minmax():
    assert _linear_interp(10, 42, 42) == 42


def test_marker_size_endpoints():
    assert marker_size(1) == 30
    assert marker_size(30) == 100


def test_marker_size_clamped():
    assert marker_size(0) == 30
    assert marker_size(1000) == 100


def test_marker_size_monotonic():
    prev = marker_size(1)
    for days in range(2, 31):
        curr = marker_size(days)
        assert curr >= prev
        prev = curr


def test_hold_frames_endpoints():
    assert hold_frames_for_days(1) == MIN_HOLD_FRAMES
    assert hold_frames_for_days(30) == MAX_HOLD_FRAMES


def test_hold_frames_clamped():
    assert hold_frames_for_days(0) == MIN_HOLD_FRAMES
    assert hold_frames_for_days(1000) == MAX_HOLD_FRAMES


def test_hold_frames_monotonic():
    prev = hold_frames_for_days(1)
    for days in range(2, 31):
        curr = hold_frames_for_days(days)
        assert curr >= prev
        prev = curr


# ---- bounce_multiplier ------------------------------------------------------


def test_bounce_multiplier_endpoints():
    assert abs(bounce_multiplier(0, 12, 3, BOUNCE_AMP) - 1) < 1e-9
    assert abs(bounce_multiplier(12, 12, 3, BOUNCE_AMP) - 1) < 1e-9


def test_bounce_multiplier_three_excursions():
    total = 12
    threshold = 0.02
    in_excursion = False
    excursions = 0
    for f in range(total + 1):
        if abs(bounce_multiplier(f, total, 3, 0.25) - 1) > threshold:
            if not in_excursion:
                excursions += 1
                in_excursion = True
        else:
            in_excursion = False
    assert excursions >= 3


def test_bounce_multiplier_decaying():
    total = 24
    first = abs(bounce_multiplier(total // 6, total, 3, 0.25) - 1)
    last = abs(bounce_multiplier(5 * total // 6, total, 3, 0.25) - 1)
    assert first > last


# ---- position_start_frames / total_frames -----------------------------------


def test_position_start_frames_empty():
    assert position_start_frames([]) == []


def test_position_start_frames_single():
    assert position_start_frames([Position(date="a", lat=0, lng=0, days=1)]) == [0]


def test_position_start_frames_offset():
    positions = [Position(date=str(i), lat=0, lng=0, days=1) for i in range(3)]
    offset = FLY_IN_FRAMES - FLY_IN_OVERLAP
    assert position_start_frames(positions) == [0, offset, 2 * offset]


def test_position_start_frames_overlap():
    positions = [
        Position(date="a", lat=0, lng=0, days=1),
        Position(date="b", lat=0, lng=0, days=1),
    ]
    assert position_start_frames(positions)[1] < FLY_IN_FRAMES


def test_total_frames():
    single = [Position(date="a", lat=0, lng=0, days=1)]
    want = FLY_IN_FRAMES + BOUNCE_FRAMES + hold_frames_for_days(1) + FINAL_HOLD
    assert total_frames(single) == want
    assert total_frames([]) == FINAL_HOLD

    two = [Position(date="a", lat=0, lng=0, days=1), Position(date="b", lat=0, lng=0, days=1)]
    assert total_frames(two) < 2 * total_frames(single)

    short = [Position(date="a", lat=0, lng=0, days=1), Position(date="b", lat=0, lng=0, days=1)]
    long = [Position(date="a", lat=0, lng=0, days=30), Position(date="b", lat=0, lng=0, days=30)]
    assert total_frames(short) < total_frames(long)


# ---- scale_image / logo -----------------------------------------------------


def test_scale_image_output_size():
    src = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    for size in (30, 50, 80, 100):
        got = scale_image(src, size)
        assert got.size == (size, size)


def test_load_logo():
    logo = load_logo()
    assert logo.mode == "RGBA"
    assert logo.size == (400, 400)


# ---- generate_animation -----------------------------------------------------


def test_generate_animation_requires_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("syndicator.animatemap.shutil.which", lambda _: None)
    journey = JourneyMap(positions=[Position(date="2025-09-13", lat=45.5, lng=13.6, days=5)])
    with pytest.raises(RuntimeError, match="ffmpeg"):
        generate_animation(journey, tmp_path / "out.mp4")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_generate_animation_produces_file(tmp_path: Path):
    journey = JourneyMap(
        positions=[
            Position(date="2025-09-13", lat=45.5127, lng=13.5954, days=10),
            Position(date="2026-01-17", lat=43.5088, lng=16.4402, days=5),
        ]
    )
    output = tmp_path / "journey.mp4"
    # Solid base map avoids hitting OSM during unit tests.
    base = Image.new("RGBA", (IMG_WIDTH, IMG_HEIGHT), (200, 220, 240, 255))
    logo = Image.new("RGBA", (40, 40), (255, 0, 0, 200))
    generate_animation(journey, output, base_map=base, logo=logo)
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
