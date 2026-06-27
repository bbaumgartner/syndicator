"""Tests for media adaptation (Pillow crops, ffmpeg video conversion)."""

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from syndicator.config import ImageSpec, VideoSpec
from syndicator.model import MediaRef
from syndicator.nodes.media_adapt import (
    CropFocus,
    _fit_without_upscale,
    adapt_image,
    adapt_media_for_channel,
    adapt_path_for_channel,
    adapt_video,
    channel_rewrites_filenames,
    crop_box,
    image_output_name,
    output_basename,
    probe_video,
    video_output_name,
)

from conftest import FakeLLM, make_cfg

FFMPEG = shutil.which("ffmpeg") is not None

IG_SPEC = ImageSpec(mode="convert", aspect="4:5", width=1080, height=1350, quality=90)


def make_image(path: Path, size=(1600, 900), mode="RGB", color=(10, 120, 200)):
    Image.new(mode, size, color).save(path)
    return path


def test_crop_box_landscape_center():
    # 1600x900 to 4:5 -> crop 720x900 centered
    box = crop_box(1600, 900, 1080 / 1350, CropFocus())
    assert box == (440, 0, 1160, 900)


def test_crop_box_focus_clamped():
    box = crop_box(1600, 900, 1080 / 1350, CropFocus(x=0.0, y=0.5))
    assert box[0] == 0  # clamped to left edge
    box = crop_box(1600, 900, 1080 / 1350, CropFocus(x=1.0, y=0.5))
    assert box[2] == 1600  # clamped to right edge


def test_adapt_image_portrait_crop(tmp_path: Path):
    src = make_image(tmp_path / "wide.jpg", (1600, 900))
    out = adapt_image(src, IG_SPEC, tmp_path / "out" / "wide.jpg")
    with Image.open(out) as im:
        assert im.size == (720, 900)
        assert im.mode == "RGB"


def test_adapt_image_downscales_when_crop_exceeds_target(tmp_path: Path):
    src = make_image(tmp_path / "huge.jpg", (4000, 3000))
    out = adapt_image(src, IG_SPEC, tmp_path / "ig.jpg")
    with Image.open(out) as im:
        assert im.size == (1080, 1350)


def test_fit_without_upscale():
    assert _fit_without_upscale(700, 394, 1920, 1080) == (700, 394)
    assert _fit_without_upscale(3000, 1688, 1920, 1080) == (1918, 1080)


def test_adapt_image_max_edge_downscale(tmp_path: Path):
    spec = ImageSpec(max_edge=2048)
    src = make_image(tmp_path / "big.jpg", (4000, 3000))
    out = adapt_image(src, spec, tmp_path / "big_out.jpg")
    with Image.open(out) as im:
        assert max(im.size) == 2048

    # Small images stay untouched in size.
    small = make_image(tmp_path / "small.jpg", (800, 600))
    out2 = adapt_image(small, spec, tmp_path / "small_out.jpg")
    with Image.open(out2) as im:
        assert im.size == (800, 600)


def test_adapt_image_png_alpha_flattened(tmp_path: Path):
    src = tmp_path / "alpha.png"
    Image.new("RGBA", (500, 500), (255, 0, 0, 128)).save(src)
    out = adapt_image(src, ImageSpec(max_edge=2048), tmp_path / "alpha.jpg")
    with Image.open(out) as im:
        assert im.mode == "RGB"


def make_video(path: Path, seconds=2, size="320x240"):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"testsrc=duration={seconds}:size={size}:rate=10",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_adapt_video_reel_crop(tmp_path: Path):
    src = make_video(tmp_path / "clip.mp4", size="640x320")
    spec = VideoSpec(aspect="9:16", width=540, height=960, max_seconds=90, pad_mode="crop")
    out = adapt_video(src, spec, tmp_path / "reel.mp4")
    info = probe_video(out)
    assert (info["width"], info["height"]) == (180, 320)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_adapt_video_focal_crop(tmp_path: Path):
    src = make_video(tmp_path / "tall.mp4", size="320x640")
    spec = VideoSpec(aspect="16:9", width=640, height=360, pad_mode="crop")
    out = adapt_video(src, spec, tmp_path / "landscape.mp4", CropFocus(x=0.5, y=0.25))
    info = probe_video(out)
    assert (info["width"], info["height"]) == (320, 180)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_adapt_video_trim(tmp_path: Path):
    src = make_video(tmp_path / "long.mp4", seconds=4)
    spec = VideoSpec(max_seconds=2)
    out = adapt_video(src, spec, tmp_path / "trimmed.mp4")
    assert probe_video(out)["duration"] <= 2.5


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_adapt_video_passthrough_copy(tmp_path: Path):
    src = make_video(tmp_path / "ok.mp4", seconds=1)
    spec = VideoSpec(max_seconds=140)
    out = adapt_video(src, spec, tmp_path / "copy.mp4")
    assert out.read_bytes() == src.read_bytes()


def test_image_output_name_copy_mode():
    spec = ImageSpec(mode="copy")
    assert image_output_name("photo.png", spec) == "photo.png"
    assert image_output_name("clip.jpg", spec) == "clip.jpg"


def test_image_output_name_convert_mode():
    spec = ImageSpec(mode="convert", format="jpeg")
    assert image_output_name("photo.png", spec) == "photo.jpg"


def test_video_output_name():
    assert video_output_name("clip.mov", VideoSpec(aspect="16:9", width=1920, height=1080)) == "clip.mp4"
    assert video_output_name("clip.mp4", VideoSpec(max_seconds=140)) == "clip.mp4"
    assert video_output_name("clip.mov", VideoSpec()) == "clip.mov"


def test_channel_rewrites_filenames(tmp_path):
    cfg = make_cfg(tmp_path)
    hugo = cfg.shared.channels["hugo"]
    assert channel_rewrites_filenames(hugo) is True  # videos crop to .mp4
    instagram = cfg.shared.channels["instagram"]
    assert channel_rewrites_filenames(instagram) is True  # images convert to .jpg


def test_adapt_media_for_channel_dispatch(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    llm = FakeLLM()
    out_dir = tmp_path / "out"

    img = make_image(tmp_path / "photo.jpg")
    media = MediaRef(kind="image", source_path=img, filename="photo.jpg")
    out = adapt_media_for_channel(media, "instagram", cfg, out_dir, llm)
    with Image.open(out) as im:
        assert im.size == (720, 900)

    landscape = make_image(tmp_path / "portrait.jpg", (900, 1600))
    out_hugo = adapt_path_for_channel(landscape, "hugo", cfg, tmp_path / "hugo", llm)
    with Image.open(out_hugo) as im:
        assert im.size == (900, 1600)
    assert out_hugo.name == "portrait.jpg"

    yt = MediaRef(kind="youtube", url="https://youtu.be/abc")
    assert adapt_media_for_channel(yt, "facebook", cfg, out_dir, llm) is None

    missing = MediaRef(kind="image", source_path=tmp_path / "nope.jpg", filename="nope.jpg")
    assert adapt_media_for_channel(missing, "facebook", cfg, out_dir, llm) is None
