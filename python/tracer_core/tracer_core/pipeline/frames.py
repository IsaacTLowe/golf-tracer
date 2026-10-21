"""Frame access at detection resolution."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

DETECTION_LONG_EDGE = 960


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    frame_count: int
    duration_s: float

    @property
    def scale(self) -> float:
        """Factor from full (normalized-video) pixels to detection pixels."""
        return min(1.0, DETECTION_LONG_EDGE / max(self.width, self.height))

    @property
    def det_size(self) -> tuple[int, int]:
        s = self.scale
        return round(self.width * s), round(self.height * s)


def video_info(path: Path, fps: float | None = None, duration_s: float | None = None) -> VideoInfo:
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"cannot open {path}")
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        f = fps or float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()
    dur = duration_s if duration_s else (n / f if n else 0.0)
    if n <= 0:
        n = round(dur * f)
    return VideoInfo(width=w, height=h, fps=f, frame_count=n, duration_s=dur)


def to_det(frame: np.ndarray, info: VideoInfo, *, gray: bool = True) -> np.ndarray:
    w, h = info.det_size
    if (frame.shape[1], frame.shape[0]) != (w, h):
        frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
    if gray and frame.ndim == 3:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return frame


def iter_frames(
    path: Path,
    info: VideoInfo,
    *,
    start: int = 0,
    stop: int | None = None,
    step: int = 1,
    gray: bool = True,
    full: bool = False,
) -> Iterator[tuple[int, np.ndarray]]:
    """Yield (frame_index, frame) at detection scale (or full scale when ``full``)."""
    cap = cv2.VideoCapture(str(path))
    try:
        idx = 0
        while idx < start:
            if not cap.grab():
                return
            idx += 1
        while stop is None or idx < stop:
            if (idx - start) % step == 0:
                ok, frame = cap.read()
                if not ok:
                    return
                if not full:
                    frame = to_det(frame, info, gray=gray)
                elif gray:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                yield idx, frame
            elif not cap.grab():
                return
            idx += 1
    finally:
        cap.release()
