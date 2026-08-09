"""Thin HTTP API around pyautoflip for n8n Adapt Reel Media."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Literal, Optional

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

FILES_ROOT = Path(os.environ.get("PYAUTOFLIP_FILES_ROOT", "/files")).resolve()
# Final encode quality (libx264). Lower = better quality / larger files.
ENCODE_CRF = int(os.environ.get("PYAUTOFLIP_CRF", "18"))
ENCODE_PRESET = os.environ.get("PYAUTOFLIP_PRESET", "medium")

app = FastAPI(title="pyautoflip sidecar", version="1.0.0")

_pyautoflip_patched = False


class ReframeRequest(BaseModel):
    input_path: str
    output_path: str
    aspect_ratio: str = Field(default="9:16", examples=["9:16", "4:5"])
    method: Literal["saliency", "detection"] = "saliency"
    motion_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    padding_method: Literal["blur", "solid_color"] = "blur"


class ReframeResponse(BaseModel):
    output_path: str
    width: int
    height: int
    duration_ms: int


def _resolve_under_files(raw: str) -> Path:
    path = Path(raw).resolve()
    try:
        path.relative_to(FILES_ROOT)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"path must be under {FILES_ROOT}: {raw}",
        ) from exc
    return path


def _make_even(n: int) -> int:
    return max(2, n - (n % 2))


def _narrow_scene_crop_width(
    scene_bboxes,  # noqa: ARG001 — match upstream signature
    frame_w: int,
    frame_h: int,
    target_aspect: tuple[int, int],
) -> int:
    """Always use exact-AR crop width (never upstream's +30% wide crop)."""
    aspect_w, aspect_h = target_aspect
    narrow_w = _make_even(int(frame_h * aspect_w / aspect_h))
    return min(narrow_w, frame_w)


def _apply_padding_to_crop_full_bleed(
    frame_bgr: np.ndarray,
    crop: tuple[int, int, int, int],
    target_aspect: tuple[int, int],
    method: str = "blur",  # noqa: ARG001 — kept for API compat; we never letterbox
) -> np.ndarray:
    """Crop to exact target AR without stretch or letterbox bars.

    Upstream saliency padding stretches the wide crop then darkens top/bottom
    (~black bars). Prefer center-crop to fill the frame instead.
    """
    cx, cy, cw, ch = crop
    crop_region = frame_bgr[cy : cy + ch, cx : cx + cw]
    if crop_region.size == 0:
        raise ValueError(f"empty crop region: {crop}")

    aspect_w, aspect_h = target_aspect
    target_ratio = aspect_w / aspect_h
    h, w = crop_region.shape[:2]
    content_ratio = w / h if h else target_ratio

    if abs(content_ratio - target_ratio) < 0.01:
        eh, ew = _make_even(h), _make_even(w)
        return crop_region[:eh, :ew]

    if content_ratio > target_ratio:
        # Too wide → trim left/right.
        new_w = min(_make_even(int(h * target_ratio)), _make_even(w))
        x0 = max(0, (w - new_w) // 2)
        return crop_region[:, x0 : x0 + new_w]
    # Too tall → trim top/bottom.
    new_h = min(_make_even(int(w / target_ratio)), _make_even(h))
    y0 = max(0, (h - new_h) // 2)
    return crop_region[y0 : y0 + new_h, :]


class FFmpegVideoWriter:
    """Single-pass libx264 writer; drops OpenCV's lossy mp4v intermediate."""

    def __init__(
        self,
        output_path: str,
        fps: float = 30.0,
        audio_path: Optional[str] = None,
        codec: str = "mp4v",  # noqa: ARG002 — upstream API compat
        frame_size: Optional[tuple[int, int]] = None,
    ):
        self.output_path = output_path
        self.fps = fps if fps and fps > 0 else 30.0
        self.audio_path = audio_path
        self.frame_size = frame_size
        self.proc: Optional[subprocess.Popen] = None
        self.frame_count = 0
        self.total_expected_frames = None
        self.input_frame_count = None
        self.input_duration = None

    def set_input_metadata(self, frame_count: int, duration: float) -> None:
        self.input_frame_count = frame_count
        self.input_duration = duration

    def _start(self, width: int, height: int) -> None:
        self.frame_size = (width, height)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(self.fps),
            "-i",
            "-",
        ]
        if self.audio_path and os.path.exists(self.audio_path):
            cmd += ["-i", self.audio_path]

        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            ENCODE_PRESET,
            "-crf",
            str(ENCODE_CRF),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
        ]
        if self.audio_path and os.path.exists(self.audio_path):
            cmd += [
                "-c:a",
                "aac",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0?",
                "-shortest",
            ]
        else:
            cmd += ["-an"]

        cmd.append(self.output_path)
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def write_frame(self, frame: np.ndarray) -> None:
        if frame.dtype != np.uint8:
            if np.issubdtype(frame.dtype, np.floating):
                frame = cv2.convertScaleAbs(frame, alpha=255.0)
            else:
                frame = cv2.convertScaleAbs(frame)

        if len(frame.shape) == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.shape[2] == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)

        height, width = frame.shape[:2]
        if self.proc is None:
            self._start(width, height)

        assert self.frame_size is not None and self.proc is not None
        target_w, target_h = self.frame_size
        if width != target_w or height != target_h:
            frame = cv2.resize(frame, self.frame_size)

        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(frame.tobytes())
        except BrokenPipeError as exc:
            err = self.proc.stderr.read().decode("utf-8", errors="replace") if self.proc.stderr else ""
            raise RuntimeError(f"ffmpeg encode failed: {err or exc}") from exc
        self.frame_count += 1

    def finalize(self) -> str:
        if self.proc is None:
            raise ValueError("No frames have been written")

        assert self.proc.stdin is not None
        self.proc.stdin.close()
        stderr = self.proc.stderr.read() if self.proc.stderr else b""
        code = self.proc.wait()
        self.proc = None
        if code != 0:
            raise RuntimeError(
                f"ffmpeg encode exited {code}: {stderr.decode('utf-8', errors='replace')}"
            )
        if not os.path.isfile(self.output_path):
            raise RuntimeError(f"ffmpeg produced no output: {self.output_path}")
        return self.output_path

    def __del__(self) -> None:
        if self.proc is not None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass


def _patch_pyautoflip() -> None:
    """Work around upstream saliency / encode quirks until pyautoflip ships fixes.

    1. Never render split-screen; keep saliency single-crop windows instead.
    2. Map 4:5 correctly (upstream `_aspect_ratio_to_tuple` falls back to 3:4).
    3. Never use +30% wide crop (avoids letterbox path).
    4. Replace stretch-and-darken padding with full-bleed center-crop.
    5. Encode with a single libx264 pass (skip OpenCV mp4v intermediate).
    """
    global _pyautoflip_patched
    if _pyautoflip_patched:
        return

    from pyautoflip.cropping import saliency_cropper as saliency_mod
    from pyautoflip.cropping.saliency_cropper import SaliencyCropper
    from pyautoflip.core import processor as processor_mod
    from pyautoflip.utils import video as video_mod

    def _never_split_screen(self) -> bool:  # noqa: ARG001
        return False

    def _aspect_ratio_to_tuple(self) -> tuple[int, int]:
        ratio_map = {
            0.5625: (9, 16),
            0.8: (4, 5),
            1.0: (1, 1),
            0.75: (3, 4),
            1.7778: (16, 9),
        }
        for ratio, dims in ratio_map.items():
            if abs(self.target_aspect_ratio - ratio) < 0.01:
                return dims
        return (int(self.target_aspect_ratio * 16), 16)

    SaliencyCropper.needs_split_screen = _never_split_screen  # type: ignore[method-assign]
    SaliencyCropper._aspect_ratio_to_tuple = _aspect_ratio_to_tuple  # type: ignore[method-assign]
    saliency_mod.compute_scene_crop_width = _narrow_scene_crop_width
    saliency_mod.apply_padding_to_crop = _apply_padding_to_crop_full_bleed
    video_mod.VideoWriter = FFmpegVideoWriter  # type: ignore[misc, assignment]
    processor_mod.VideoWriter = FFmpegVideoWriter  # type: ignore[misc, assignment]
    _pyautoflip_patched = True


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/reframe", response_model=ReframeResponse)
def reframe(body: ReframeRequest) -> ReframeResponse:
    input_path = _resolve_under_files(body.input_path)
    output_path = _resolve_under_files(body.output_path)

    if not input_path.is_file():
        raise HTTPException(status_code=404, detail=f"input not found: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Import lazily so /health stays cheap before models are warm.
    from pyautoflip import reframe_video

    _patch_pyautoflip()

    started = time.perf_counter()
    try:
        result = reframe_video(
            input_path=str(input_path),
            output_path=str(output_path),
            target_aspect_ratio=body.aspect_ratio,
            detection_method=body.method,
            motion_threshold=body.motion_threshold,
            padding_method=body.padding_method,
            debug_mode=False,
        )
    except Exception as exc:  # noqa: BLE001 — surface as HTTP 500 for n8n
        raise HTTPException(status_code=500, detail=f"reframe failed: {exc}") from exc

    out = Path(result).resolve()
    if not out.is_file():
        raise HTTPException(status_code=500, detail=f"output missing after reframe: {out}")

    cap = cv2.VideoCapture(str(out))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()

    duration_ms = int((time.perf_counter() - started) * 1000)
    return ReframeResponse(
        output_path=str(out),
        width=width,
        height=height,
        duration_ms=duration_ms,
    )
