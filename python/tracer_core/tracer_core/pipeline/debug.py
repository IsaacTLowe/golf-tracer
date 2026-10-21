"""TRACE_DEBUG video: candidates, chosen track and fitted curve over the clip (detection scale)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np
from tracer_spec.render import Rasterizer

from .. import media
from .frames import VideoInfo, iter_frames
from .track import Blob

if TYPE_CHECKING:
    from . import TraceResult


def write_debug_video(
    path: Path,
    info: VideoInfo,
    out: Path,
    result: TraceResult,
    frame_ids: list[int],
    blobs: list[list[Blob]],
    ball_r: float,
) -> None:
    w, h = info.det_size
    w2, h2 = w - w % 2, h - h % 2
    enc = media.encoder_process(out, w2, h2, f"{info.fps:.6f}", None)
    assert enc.stdin is not None
    blobs_by_frame = {fid: blobs[k] for k, fid in enumerate(frame_ids) if k < len(blobs)}
    track_by_frame = {round(t * info.fps): (x * w, y * h) for t, x, y in result.observed}
    rast = Rasterizer(result.tracer, w, h) if result.tracer else None
    trail: list[tuple[float, float]] = []
    impact_frame = round(result.impact_time_s * info.fps) if result.impact_time_s is not None else None
    try:
        for i, frame in iter_frames(path, info, gray=False):
            t = i / info.fps
            if rast is not None:
                frame = rast.draw(frame, t)
            if result.ball_xy is not None:
                bx, by = result.ball_xy[0] * w, result.ball_xy[1] * h
                cv2.circle(frame, (int(bx), int(by)), int(ball_r * 2 + 3), (0, 255, 255), 1, cv2.LINE_AA)
            for b in blobs_by_frame.get(i, []):
                cv2.circle(frame, (int(b.x), int(b.y)), 3, (160, 160, 160), 1)
            if i in track_by_frame:
                trail.append(track_by_frame[i])
            for p in trail:
                cv2.circle(frame, (int(p[0]), int(p[1])), 2, (0, 255, 0), -1)
            label = f"f{i} t={t:.3f}s conf={result.confidence:.2f} {result.route}"
            if impact_frame is not None and i >= impact_frame:
                label += f"  impact@{result.impact_time_s:.3f} ({result.impact_source})"
            cv2.putText(frame, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(frame, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            enc.stdin.write(np.ascontiguousarray(frame[:h2, :w2]).tobytes())
    finally:
        enc.stdin.close()
        enc.wait(timeout=300)
