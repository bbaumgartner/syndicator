"""media_adapt node: adapt images and videos to platform specs.

Images via Pillow: EXIF-orientation fix, metadata strip, aspect crop (e.g.
Instagram 4:5 portrait) with an optional vision-LLM focal point, resize,
JPEG output. Videos via ffmpeg: aspect conversion with blurred padding
(e.g. 9:16 reels), duration caps, H.264/AAC transcode.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageOps
from pydantic import BaseModel

from .. import config as config_mod
from ..config import Config, ImageSpec, VideoSpec
from ..llm import LLMClient, image_data_url
from ..model import MediaRef

log = logging.getLogger(__name__)


class CropFocus(BaseModel):
    x: float = 0.5
    y: float = 0.5


def get_crop_focus(path: Path, cfg: Config, llm: LLMClient) -> CropFocus:
    """Ask a vision model for the focal point; center on failure."""
    if not cfg.shared.media.crop_focus.enabled:
        return CropFocus()
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((512, 512))
            preview = path.parent / f".crop_preview_{path.stem}.jpg"
            im.convert("RGB").save(preview, "JPEG", quality=70)
        system = (config_mod.REPO_ROOT / "prompts" / "crop_focus.md").read_text(encoding="utf-8")
        user_content = [
            {"type": "text", "text": "Photo to analyze:"},
            {"type": "image_url", "image_url": {"url": image_data_url(preview)}},
        ]
        preview.unlink(missing_ok=True)
        focus = llm.complete_structured(
            node="crop_focus",
            model=cfg.shared.media.crop_focus.model,
            system=system,
            user_content=user_content,
            schema=CropFocus,
        )
        return CropFocus(x=min(max(focus.x, 0.0), 1.0), y=min(max(focus.y, 0.0), 1.0))
    except Exception as err:  # noqa: BLE001 - crop focus is best-effort
        log.warning("crop focus failed for %s (%s) — using center", path.name, err)
        return CropFocus()


def crop_box(width: int, height: int, target_ratio: float, focus: CropFocus) -> tuple[int, int, int, int]:
    """Largest crop window with the target ratio, centered on the focus point."""
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


def adapt_image(src: Path, spec: ImageSpec, out_path: Path, focus: CropFocus | None = None) -> Path:
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)

        if spec.width and spec.height:
            target_ratio = spec.width / spec.height
            box = crop_box(im.width, im.height, target_ratio, focus or CropFocus())
            im = im.crop(box).resize((spec.width, spec.height), Image.LANCZOS)
        elif spec.max_edge and max(im.size) > spec.max_edge:
            im.thumbnail((spec.max_edge, spec.max_edge), Image.LANCZOS)

        if im.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", im.size, (255, 255, 255))
            rgba = im.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            im = background
        elif im.mode != "RGB":
            im = im.convert("RGB")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        im.save(out_path, "JPEG", quality=spec.quality, optimize=True)
    return out_path


def probe_video(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    stream = (data.get("streams") or [{}])[0]
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
    }


def adapt_video(src: Path, spec: VideoSpec, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    info = probe_video(src)

    needs_aspect = bool(spec.aspect and spec.width and spec.height)
    needs_trim = bool(spec.max_seconds and info["duration"] > spec.max_seconds)

    if not needs_aspect and not needs_trim and src.suffix.lower() == ".mp4":
        shutil.copyfile(src, out_path)
        return out_path

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if needs_trim:
        cmd += ["-t", str(spec.max_seconds)]

    if needs_aspect:
        w, h = spec.width, spec.height
        if spec.pad_mode == "blur":
            vf = (
                f"split[a][b];"
                f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},boxblur=20:5[bg];"
                f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
            )
        else:
            vf = (
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
            )
        cmd += ["-vf", vf]

    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def adapt_media_for_channel(
    media: MediaRef,
    channel: str,
    cfg: Config,
    out_dir: Path,
    llm: LLMClient,
) -> Path | None:
    """Adapt one media file for a channel; returns the output path.

    YouTube references and missing files return None (they are carried as
    links / skipped by the caller).
    """
    if media.kind == "youtube" or media.source_path is None or not media.source_path.exists():
        return None

    ch_cfg = cfg.shared.channels[channel]
    src = media.source_path

    if media.kind == "image":
        spec = ch_cfg.image
        out_path = out_dir / f"{src.stem}.jpg"
        focus = None
        if spec.width and spec.height:
            focus = get_crop_focus(src, cfg, llm)
        return adapt_image(src, spec, out_path, focus)

    spec = ch_cfg.video
    out_path = out_dir / f"{src.stem}.mp4"
    try:
        return adapt_video(src, spec, out_path)
    except subprocess.CalledProcessError as err:
        stderr = err.stderr.decode() if isinstance(err.stderr, bytes) else err.stderr
        log.error("ffmpeg failed for %s: %s", src.name, stderr)
        return None
