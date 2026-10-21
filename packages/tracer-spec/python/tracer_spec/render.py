"""Rasterize a tracer frame plan onto a video frame (numpy + OpenCV).

Drawing model, shared with the canvas renderer in ``ts/src/canvas.ts``:

* Each consecutive pair of plan points is stroked as a capsule (a line with round caps)
  whose width is the mean of the two point widths times the frame height.
* The glow underlay is the same polyline stroked wider, Gaussian-blurred and composited
  under the main stroke at ``GLOW_OPACITY * glow * alpha``.
* The main stroke is drawn opaque into its own layer and composited at ``alpha`` so
  overlapping caps never double up.

Coverage is anti-aliased analytically: ``clip(r + 0.5 - d, 0, 1)`` with ``d`` the distance
from the pixel centre to the segment, accumulated over segments with source-over
(``1 - (1 - a)(1 - b)``) like a canvas stroking them one at a time.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from .spec import GLOW_BLUR_FACTOR, GLOW_OPACITY, GLOW_WIDTH_FACTOR, FramePlan, frame_plan


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _stamp_segment(mask: np.ndarray, a: np.ndarray, b: np.ndarray, r: float) -> None:
    h, w = mask.shape
    pad = r + 1.5
    x0 = max(int(np.floor(min(a[0], b[0]) - pad)), 0)
    x1 = min(int(np.ceil(max(a[0], b[0]) + pad)), w)
    y0 = max(int(np.floor(min(a[1], b[1]) - pad)), 0)
    y1 = min(int(np.ceil(max(a[1], b[1]) + pad)), h)
    if x0 >= x1 or y0 >= y1:
        return
    xs = np.arange(x0, x1, dtype=np.float64) + 0.5
    ys = np.arange(y0, y1, dtype=np.float64) + 0.5
    px = xs[None, :] - a[0]
    py = ys[:, None] - a[1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    ll = dx * dx + dy * dy
    t = np.clip((px * dx + py * dy) / ll, 0.0, 1.0) if ll > 0 else np.zeros_like(px * py)
    ex = px - t * dx
    ey = py - t * dy
    d = np.sqrt(ex * ex + ey * ey)
    cov = np.clip(r + 0.5 - d, 0.0, 1.0).astype(np.float32)
    region = mask[y0:y1, x0:x1]
    # Source-over accumulation, exactly what a canvas does when it strokes the segments one by
    # one: overlapping anti-aliased edges compound. It is order independent, so incremental
    # stamping (Rasterizer) gives the same result.
    region[:] = 1.0 - (1.0 - region) * (1.0 - cov)


def stroke_mask(points_px: np.ndarray, widths_px: np.ndarray, shape: tuple[int, int], scale: float = 1.0) -> np.ndarray:
    """Coverage mask (float32, 0..1) of the tapered polyline."""
    mask = np.zeros(shape, dtype=np.float32)
    for k in range(len(points_px) - 1):
        r = 0.5 * scale * 0.5 * (widths_px[k] + widths_px[k + 1])
        _stamp_segment(mask, points_px[k], points_px[k + 1], r)
    return mask


def _plan_px(plan: FramePlan, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    pts = np.asarray(plan["points"], dtype=np.float64) * np.array([w, h], dtype=np.float64)
    widths = np.asarray(plan["widths"], dtype=np.float64) * h
    return pts, widths


def _bbox(pts: np.ndarray, widths: np.ndarray, reach_extra: float, w: int, h: int) -> tuple[int, int, int, int] | None:
    reach = float(widths.max()) * 0.5 + reach_extra
    x0 = max(int(np.floor(pts[:, 0].min() - reach)), 0)
    x1 = min(int(np.ceil(pts[:, 0].max() + reach)), w)
    y0 = max(int(np.floor(pts[:, 1].min() - reach)), 0)
    y1 = min(int(np.ceil(pts[:, 1].max() + reach)), h)
    if x0 >= x1 or y0 >= y1:
        return None
    return x0, y0, x1, y1


class _Look:
    """Per-style constants for one frame size."""

    def __init__(self, style: dict[str, Any], h: int, bgr: bool):
        rgb = hex_to_rgb(style["color"])
        self.color = np.array(rgb[::-1] if bgr else rgb, dtype=np.float32)
        self.glow = float(style["glow"])
        self.glow_scale = 1.0 + GLOW_WIDTH_FACTOR * self.glow
        self.sigma = self.glow * GLOW_BLUR_FACTOR * float(style["width"]) * h

    def reach_extra(self, max_width: float) -> float:
        return max_width * 0.5 * (self.glow_scale - 1.0) + 3.0 * self.sigma + 3.0

    def blur(self, gmask: np.ndarray) -> np.ndarray:
        if self.sigma > 0.3:
            blurred: np.ndarray = cv2.GaussianBlur(
                gmask, (0, 0), sigmaX=self.sigma, sigmaY=self.sigma, borderType=cv2.BORDER_CONSTANT
            )
            return blurred
        return gmask


def _composite(
    frame: np.ndarray,
    box: tuple[int, int, int, int],
    gmask: np.ndarray | None,
    mmask: np.ndarray,
    look: _Look,
    alpha: float,
) -> np.ndarray:
    x0, y0, x1, y1 = box
    out: np.ndarray = frame[y0:y1, x0:x1].astype(np.float32)
    if gmask is not None:
        ga = (gmask * (GLOW_OPACITY * look.glow * alpha))[..., None]
        out = out * (1.0 - ga) + look.color * ga
    ma = (mmask * alpha)[..., None]
    out = out * (1.0 - ma) + look.color * ma
    result = frame.copy()
    result[y0:y1, x0:x1] = np.clip(out + 0.5, 0, 255).astype(np.uint8)
    return result


def draw_plan(frame: np.ndarray, plan: FramePlan | None, style: dict[str, Any], *, bgr: bool = True) -> np.ndarray:
    """Composite one frame plan onto ``frame`` (H x W x 3 uint8). Returns a new array.

    Stateless reference implementation; ``Rasterizer`` produces identical output faster.
    """
    if plan is None or len(plan["points"]) < 2:
        return frame
    h, w = frame.shape[:2]
    pts, widths = _plan_px(plan, w, h)
    look = _Look(style, h, bgr)
    box = _bbox(pts, widths, look.reach_extra(float(widths.max())), w, h)
    if box is None:
        return frame
    x0, y0, x1, y1 = box
    local = pts - np.array([x0, y0], dtype=np.float64)
    shape = (y1 - y0, x1 - x0)
    gmask = look.blur(stroke_mask(local, widths, shape, scale=look.glow_scale)) if look.glow > 0 else None
    mmask = stroke_mask(local, widths, shape)
    return _composite(frame, box, gmask, mmask, look, float(plan["alpha"]))


def draw_tracer(frame: np.ndarray, tracer: dict[str, Any], t: float, *, bgr: bool = True) -> np.ndarray:
    return draw_plan(frame, frame_plan(tracer, t), tracer["style"], bgr=bgr)


class Rasterizer:
    """Draws one tracer onto consecutive frames of one size, reusing work between frames.

    The fixed part of the polyline (every sample behind the ball head) only grows, so its
    coverage masks are stamped incrementally; after landing the finished masks are cached.
    Because coverage accumulates order-independently, the result equals ``draw_tracer``.
    """

    def __init__(self, tracer: dict[str, Any], width: int, height: int, *, bgr: bool = True):
        self.tracer = tracer
        self.w, self.h = width, height
        self.look = _Look(tracer["style"], height, bgr)
        self._reset()

    def _reset(self) -> None:
        self.main = np.zeros((self.h, self.w), dtype=np.float32)
        self.glow = np.zeros((self.h, self.w), dtype=np.float32) if self.look.glow > 0 else None
        self.stamped = 1  # number of fixed points whose incoming segment is already stamped
        self.final: tuple[tuple[int, int, int, int], np.ndarray | None, np.ndarray] | None = None

    def _stamp(self, pts: np.ndarray, widths: np.ndarray, upto: int) -> None:
        for k in range(self.stamped - 1, upto - 1):
            r = 0.5 * 0.5 * (widths[k] + widths[k + 1])
            _stamp_segment(self.main, pts[k], pts[k + 1], r)
            if self.glow is not None:
                _stamp_segment(self.glow, pts[k], pts[k + 1], r * self.look.glow_scale)
        self.stamped = max(self.stamped, upto)

    def draw(self, frame: np.ndarray, t: float) -> np.ndarray:
        plan = frame_plan(self.tracer, t)
        if plan is None or len(plan["points"]) < 2:
            return frame
        alpha = float(plan["alpha"])
        if plan["head"] is None and self.final is not None:
            cached_box, cached_g, cached_m = self.final
            return _composite(frame, cached_box, cached_g, cached_m, self.look, alpha)

        pts, widths = _plan_px(plan, self.w, self.h)
        n_fixed = len(pts) - 1 if plan["head"] is not None else len(pts)
        if n_fixed < self.stamped:
            self._reset()  # time went backwards
        self._stamp(pts, widths, n_fixed)

        box = _bbox(pts, widths, self.look.reach_extra(float(widths.max())), self.w, self.h)
        if box is None:
            return frame
        x0, y0, x1, y1 = box
        m = self.main[y0:y1, x0:x1].copy()
        g = self.glow[y0:y1, x0:x1].copy() if self.glow is not None else None
        if plan["head"] is not None:
            off = np.array([x0, y0], dtype=np.float64)
            a, b = pts[-2] - off, pts[-1] - off
            r = 0.5 * 0.5 * (widths[-2] + widths[-1])
            _stamp_segment(m, a, b, r)
            if g is not None:
                _stamp_segment(g, a, b, r * self.look.glow_scale)
        if g is not None:
            g = self.look.blur(g)
        if plan["head"] is None:
            self.final = (box, g, m)
        return _composite(frame, box, g, m, self.look, alpha)
