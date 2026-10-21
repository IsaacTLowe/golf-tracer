"""Burn a tracer into a copy of the normalized video (the ``render`` job)."""

from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from tracer_spec.render import Rasterizer

from . import media

logger = logging.getLogger(__name__)


def _watermark(frame: np.ndarray, text: str) -> np.ndarray:
    h, w = frame.shape[:2]
    scale = max(0.4, h / 1600)
    thickness = max(1, round(scale * 2))
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    margin = int(h * 0.02)
    org = (w - tw - margin, h - margin)
    cv2.putText(
        frame, text, (org[0] + 1, org[1] + 1), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 1, cv2.LINE_AA
    )
    cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), thickness, cv2.LINE_AA)
    _ = th
    return frame


def render_video(src: Path, dst: Path, tracer: dict[str, Any], *, watermark: str | None = None) -> dict[str, float]:
    """Decode ``src`` frame by frame, draw the tracer at full resolution, encode H.264 with the
    original audio muxed back in. Returns timing stats."""
    started = time.perf_counter()
    probe = media.probe(src)
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise media.MediaError("unreadable", "Could not decode the normalized video.", retryable=True)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = probe.nominal_fps
    enc = media.encoder_process(dst, width, height, probe.nominal_fps_str, src if probe.has_audio else None)
    assert enc.stdin is not None
    rast = Rasterizer(tracer, width, height)
    frames = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            out = rast.draw(frame, frames / fps)
            if watermark:
                out = _watermark(out if out is not frame else frame.copy(), watermark)
            enc.stdin.write(np.ascontiguousarray(out).tobytes())
            frames += 1
    except BrokenPipeError:
        pass
    finally:
        cap.release()
        with contextlib.suppress(BrokenPipeError):
            enc.stdin.close()
    stderr = enc.stderr.read().decode(errors="replace") if enc.stderr else ""
    code = enc.wait(timeout=600)
    if code != 0 or frames == 0:
        raise media.MediaError("render_failed", f"Encoding failed ({code}): {stderr[-400:]}", retryable=True)
    elapsed = time.perf_counter() - started
    logger.info("rendered", extra={"frames": frames, "seconds": round(elapsed, 2), "w": width, "h": height})
    return {"frames": frames, "seconds": elapsed}
