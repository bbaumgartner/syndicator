"""Thin HTTP API around pyautoflip for n8n Adapt Reel Media."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Literal

import cv2
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

FILES_ROOT = Path(os.environ.get("PYAUTOFLIP_FILES_ROOT", "/files")).resolve()

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


def _patch_pyautoflip() -> None:
    """Work around upstream saliency quirks until pyautoflip ships fixes.

    1. Never render split-screen; keep saliency single-crop windows instead.
    2. Map 4:5 correctly (upstream `_aspect_ratio_to_tuple` falls back to 3:4).
    """
    global _pyautoflip_patched
    if _pyautoflip_patched:
        return

    from pyautoflip.cropping.saliency_cropper import SaliencyCropper

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
