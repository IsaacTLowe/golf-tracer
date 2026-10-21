"""Automatic tracing: address → impact → early-flight tracking → fit → confidence.

Entry point: ``auto_trace``. Coordinates inside the pipeline are detection-scale pixels;
everything returned is normalized to the frame (0..1) and in seconds on the normalized
video's timeline.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import address as addr
from . import confidence as conf
from . import impact as imp
from .fit import FitResult, fit_image_v1
from .fit3d import fit_physics
from .frames import VideoInfo, iter_frames, video_info
from .scene import estimate_horizon
from .track import MAX_TRACK_S, Blob, track

logger = logging.getLogger(__name__)

ADDRESS_SAMPLES = 30
ADDRESS_LEAD_S = 2.0  # look at this much time before the expected impact for the ball
ADDRESS_GAP_S = 0.12  # ...stopping this long before it (the club is near the ball)
TRACK_MAX_FPS = 120.0
PHYSICS_MAX_RESIDUAL_PX = 6.0


@dataclass
class FitChoice:
    tracer: dict[str, Any]
    residual_px: float


@dataclass
class TraceResult:
    tracer: dict[str, Any] | None
    confidence: float
    route: conf.Route
    impact_time_s: float | None = None
    impact_source: str | None = None
    ball_xy: tuple[float, float] | None = None
    ball_r: float | None = None  # fraction of frame height
    horizon_y: float | None = None
    observed: list[tuple[float, float, float]] = field(default_factory=list)
    reason: str | None = None
    timings: dict[str, float] = field(default_factory=dict)

    def detection(self) -> dict[str, Any]:
        """Facts worth keeping for the manual wizard and re-traces."""
        return {
            "impact_time_s": self.impact_time_s,
            "impact_source": self.impact_source,
            "ball_xy": list(self.ball_xy) if self.ball_xy else None,
            "ball_r": self.ball_r,
            "horizon_y": self.horizon_y,
            "confidence": self.confidence,
            "reason": self.reason,
        }


def auto_trace(
    path: Path,
    video: dict[str, Any],
    *,
    audio: np.ndarray | None = None,
    hints: dict[str, Any] | None = None,
    style: dict[str, Any] | None = None,
    debug_out: Path | None = None,
    version: str = "v2",
) -> TraceResult:
    """Trace one clip. ``version="v1"`` reproduces the milestone-4 pipeline (no camera-motion
    compensation, image-space fit only) for comparisons and as a fallback."""
    v2 = version != "v1"
    hints = hints or {}
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    info = video_info(path, fps=float(video["fps"]), duration_s=float(video["duration_s"]))
    det_w, det_h = info.det_size
    fps = info.fps

    def lap(name: str, since: list[float] = [t0]) -> None:  # noqa: B006
        now = time.perf_counter()
        timings[name] = round(now - since[0], 3)
        since[0] = now

    # 1. Audio onset.
    onset = imp.audio_onset(audio)
    hint_impact = hints.get("impact_time_s")
    hint_xy = hints.get("ball_xy")
    t_ref = float(hint_impact) if hint_impact is not None else (onset.t if onset else None)
    lap("audio")

    # 2. Sample pre-impact frames for the address search.
    if t_ref is not None:
        a_start = max(0.0, t_ref - ADDRESS_LEAD_S)
        a_stop = max(a_start + 1.0 / fps, t_ref - ADDRESS_GAP_S)
    else:
        a_start, a_stop = 0.0, max(0.5, 0.55 * info.duration_s)
    f_start, f_stop = int(a_start * fps), max(int(a_start * fps) + 1, int(a_stop * fps))
    step = max(1, (f_stop - f_start) // ADDRESS_SAMPLES)
    samples: list[np.ndarray] = []
    sample_idx: list[int] = []
    color_first = None
    for i, f in iter_frames(path, info, start=f_start, stop=f_stop, step=step, gray=False):
        if color_first is None:
            color_first = f
        samples.append(_gray(f))
        sample_idx.append(i)
    horizon = estimate_horizon(samples[0]) if samples else None
    hint_det = (float(hint_xy[0]) * det_w, float(hint_xy[1]) * det_h) if hint_xy else None
    clusters = addr.find_address(samples, hint=hint_det, horizon_y=horizon)
    lap("address")
    if not clusters:
        return _no_track(onset, hint_impact, fps, horizon, "ball not found at address", timings)

    # 3. Presence curves for the top candidates; the true ball is the one that disappears.
    templates = []
    for c in clusters:
        try:
            tpl, half = imp.make_template(samples, c.x, c.y, c.r)
        except ValueError:
            continue
        templates.append((c, tpl, half))
    if not templates:
        return _no_track(onset, hint_impact, fps, horizon, "ball too close to the frame edge", timings)
    curve_start = f_start
    scores: list[list[float]] = [[] for _ in templates]
    idxs: list[int] = []
    for i, g in iter_frames(path, info, start=curve_start):
        idxs.append(i)
        for j, (c, tpl, half) in enumerate(templates):
            scores[j].append(imp.presence_score(g, tpl, half, c.x, c.y, slack=max(2, int(c.r))))
    lap("presence")

    best_j, best_v, best_rank = -1, None, -1.0
    for j, (c, _tpl, _half) in enumerate(templates):
        curve = imp.PresenceCurve(np.asarray(idxs), np.asarray(scores[j]))
        v = imp.visual_impact(curve, fps)
        if hint_impact is not None:
            hf = round(float(hint_impact) * fps)
            if v is None or abs(v.frame - hf) > max(3, 0.15 * fps):
                v = _forced_visual(curve, hf, fps)
        if v is None:
            continue
        agree = 1.0
        if onset is not None:
            agree = 1.0 if abs(v.frame / fps - onset.t) <= imp.AGREE_S else 0.6
        rank = v.step * (0.5 + 0.5 * c.score) * agree
        if rank > best_rank:
            best_j, best_v, best_rank = j, v, rank
    if best_j < 0:
        chosen = clusters[0]
        decision = imp.decide(None, onset, fps)
        address_score = chosen.score * 0.5
    else:
        chosen = templates[best_j][0]
        decision = imp.decide(best_v, onset, fps)
        address_score = chosen.score
    if hint_impact is not None:
        hf = round(float(hint_impact) * fps)
        decision = imp.ImpactDecision(hf / fps, hf, "hint", 1.0)
    ball_xy = (chosen.x / det_w, chosen.y / det_h)
    ball_r = chosen.r / det_h
    if decision is None:
        res = _no_track(onset, None, fps, horizon, "impact not found", timings)
        res.ball_xy, res.ball_r = ball_xy, ball_r
        return res
    lap("impact")

    # 4. Track early flight.
    stride = max(1, math.ceil(fps / TRACK_MAX_FPS))
    t_start = max(0, decision.frame - 2 * stride)
    t_stop = min(info.frame_count, decision.frame + int(MAX_TRACK_S * fps) + 2 * stride)
    frames: list[np.ndarray] = []
    times: list[float] = []
    frame_ids: list[int] = []
    for i, g in iter_frames(path, info, start=t_start, stop=t_stop, step=stride):
        frames.append(g)
        frame_ids.append(i)
        times.append((i - decision.frame) / fps)
    impact_k = next((k for k, t in enumerate(times) if t >= 0), 0)
    track_pts: list[Any] = []
    blobs: list[list[Blob]] = []
    if len(frames) > 3:
        track_pts, blobs = track(frames, times, (chosen.x, chosen.y), chosen.r, impact_k, stabilize=v2)
    observed = [(frame_ids[p.k] / fps, p.x / det_w, p.y / det_h) for p in track_pts]
    lap("track")

    # 5. Fit and score.
    fit: FitResult | FitChoice | None = None
    vdoc = _video_doc(video, info)
    if len(observed) >= 3:
        try:
            fit = fit_image_v1(observed, ball_xy, decision.time_s, vdoc, horizon, style)
        except Exception:  # a bad fit must not kill the job; it routes to needs_input
            logger.exception("image fit failed")
    # Physics fit (3D flight through an estimated camera): better extrapolation when it
    # explains the observed track; otherwise keep the image-space fit.
    if fit is not None and len(observed) >= 5 and v2:
        try:
            phys = fit_physics(observed, ball_xy, ball_r, decision.time_s, vdoc, horizon, style)
        except Exception:
            logger.exception("physics fit failed")
            phys = None
        if phys is not None and phys.residual_px <= max(PHYSICS_MAX_RESIDUAL_PX, 2.0 * fit.residual_px):
            fit = FitChoice(tracer=phys.tracer, residual_px=phys.residual_px)
    lap("fit")
    confidence = conf.score(
        conf.ConfidenceInputs(
            address_score=address_score,
            impact_agreement=decision.agreement,
            tracked_points=len(observed) * stride,
            fps=fps,
            residual_px=fit.residual_px if fit else None,
            track_quality=conf.track_quality(ball_xy[1], [y for _, _, y in observed]),
        )
    )
    tracer = fit.tracer if fit else None
    if tracer is not None:
        tracer["fit"]["confidence"] = confidence
    route = conf.route(confidence, tracer is not None)
    result = TraceResult(
        tracer=tracer,
        confidence=confidence,
        route=route,
        impact_time_s=decision.time_s,
        impact_source=decision.source,
        ball_xy=ball_xy,
        ball_r=ball_r,
        horizon_y=horizon,
        observed=observed,
        reason=None if tracer else "ball not tracked after impact",
        timings=timings,
    )
    if debug_out is not None:
        from .debug import write_debug_video

        write_debug_video(path, info, debug_out, result, frame_ids, blobs, chosen.r)
        lap("debug")
    timings["total"] = round(time.perf_counter() - t0, 3)
    return result


def _gray(f: np.ndarray) -> np.ndarray:
    import cv2

    return cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f


def _video_doc(video: dict[str, Any], info: VideoInfo) -> dict[str, Any]:
    return {"width": info.width, "height": info.height, "fps": info.fps, "duration_s": float(video["duration_s"])}


def _forced_visual(curve: imp.PresenceCurve, frame: int, fps: float) -> imp.VisualImpact | None:
    """Presence step around a user-supplied impact frame (scores the candidate, keeps the hint)."""
    ids = curve.frames
    if not len(ids):
        return None
    k = int(np.searchsorted(ids, frame))
    pre = max(3, int(0.25 * fps))
    before = curve.ncc[max(0, k - pre) : k]
    after = curve.ncc[k : k + pre]
    if not len(before) or not len(after):
        return None
    step = float(before.mean() - after.mean())
    return imp.VisualImpact(frame=frame, step=step, before=float(before.mean()), after=float(after.mean()))


def _no_track(
    onset: imp.AudioOnset | None, hint_impact: float | None, fps: float, horizon: float | None, reason: str,
    timings: dict[str, float],
) -> TraceResult:  # fmt: skip
    impact_t = float(hint_impact) if hint_impact is not None else (round(onset.t * fps) / fps if onset else None)
    return TraceResult(
        tracer=None, confidence=0.0, route="needs_input", impact_time_s=impact_t,
        impact_source="hint" if hint_impact is not None else ("audio" if onset else None),
        horizon_y=horizon, reason=reason, timings=timings,
    )  # fmt: skip


__all__ = ["Blob", "TraceResult", "auto_trace"]
