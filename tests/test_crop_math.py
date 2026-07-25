"""Tests for crop_box / fit_without_upscale (n8n Code node reference)."""

from syndicator.crop_math import CropFocus, crop_box, fit_without_upscale


def test_crop_box_landscape_to_portrait():
    # 1600x900 → 4:5 window is 720x900, centered at default focus
    left, top, right, bottom = crop_box(1600, 900, 4 / 5, CropFocus())
    assert (right - left, bottom - top) == (720, 900)
    assert top == 0
    assert left == (1600 - 720) // 2


def test_crop_box_focus_clamped():
    # Focus near the right edge — window must stay in bounds
    left, top, right, bottom = crop_box(1000, 1000, 1.0, CropFocus(x=0.95, y=0.5))
    assert left >= 0 and top >= 0
    assert right <= 1000 and bottom <= 1000
    assert (right - left) == (bottom - top)


def test_fit_without_upscale_downscales():
    assert fit_without_upscale(2000, 2500, 1080, 1350) == (1080, 1350)


def test_fit_without_upscale_keeps_smaller():
    assert fit_without_upscale(720, 900, 1080, 1350) == (720, 900)
