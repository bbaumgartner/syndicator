"""Crop geometry helpers — reference implementation for the n8n Code node.

Media adaptation runs in n8n (Edit Image + FFmpeg). This module keeps the
focal-point crop math in-repo so unit tests and the workflow Code node stay
aligned.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CropFocus:
    x: float = 0.5  # normalized 0–1
    y: float = 0.5


def crop_box(
    width: int, height: int, target_ratio: float, focus: CropFocus
) -> tuple[int, int, int, int]:
    """Largest crop window with the target ratio, centered on the focus point.

    Returns ``(left, top, right, bottom)`` in source pixels.
    """
    src_ratio = width / height
    if src_ratio > target_ratio:
        crop_h = height
        crop_w = round(height * target_ratio)
    else:
        crop_w = width
        crop_h = round(width / target_ratio)

    left = round(focus.x * width - crop_w / 2)
    top = round(focus.y * height - crop_h / 2)
    left = min(max(left, 0), width - crop_w)
    top = min(max(top, 0), height - crop_h)
    return (left, top, left + crop_w, top + crop_h)


def even(n: int) -> int:
    """Round down to an even integer (required by yuv420p)."""
    return n if n % 2 == 0 else n - 1


def fit_without_upscale(crop_w: int, crop_h: int, max_w: int, max_h: int) -> tuple[int, int]:
    """Output size for a crop: native pixels, or scaled down to fit the cap."""
    if crop_w <= max_w and crop_h <= max_h:
        return crop_w, crop_h
    scale = min(max_w / crop_w, max_h / crop_h)
    return even(int(crop_w * scale)), even(int(crop_h * scale))
