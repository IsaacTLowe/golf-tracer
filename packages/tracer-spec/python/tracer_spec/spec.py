"""Tracer spec v1: curve, timing and drawing-plan math.

This module is the Python twin of ``packages/tracer-spec/ts/src/spec.ts``. Both must
produce identical numbers (to 1e-6) for the shared test vectors in
``packages/tracer-spec/vectors/vectors.json``. Change them together.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    import numpy as np

SCHEMA_VERSION = 1
SAMPLES_PER_SEGMENT = 200
FADE_S = 0.3
# Glow underlay: stroke width = main width * (1 + GLOW_WIDTH_FACTOR * glow),
# Gaussian blur sigma (px) = glow * GLOW_BLUR_FACTOR * base width (px),
# layer opacity = GLOW_OPACITY * glow.
GLOW_WIDTH_FACTOR = 2.0
GLOW_BLUR_FACTOR = 0.75
GLOW_OPACITY = 0.6
EPS = 1e-6

Point = list[float]

DEFAULT_STYLE: dict[str, Any] = {
    "color": "#FF3B30",
    "width": 0.006,
    "taper": 0.4,
    "glow": 0.5,
    "persist_s": 2.0,
}
DEFAULT_TIMING: dict[str, float] = {"ascent_ease": 2.0, "descent_ease": 1.3}


class FramePlan(TypedDict):
    """What to draw at one moment: a polyline in normalized coords plus per-point widths."""

    points: list[Point]
    widths: list[float]  # stroke width at each point, as a fraction of frame height
    alpha: float
    head: Point | None  # current ball position during flight, None after landing


def bezier(u: float, p0: Point, c: Point, p1: Point) -> Point:
    a = (1.0 - u) * (1.0 - u)
    b = 2.0 * (1.0 - u) * u
    d = u * u
    return [a * p0[0] + b * c[0] + d * p1[0], a * p0[1] + b * c[1] + d * p1[1]]


def sample_u(i: int) -> float:
    return i / (SAMPLES_PER_SEGMENT - 1)


def ascent_u[N: (float, "np.ndarray")](tau: N, ease: float, depth_ratio: float = 1.0) -> N:
    """Bézier parameter of the ascent at normalized time ``tau`` (0..1). ``depth_ratio`` is the
    ball's distance from the camera at impact divided by its distance at the apex: below 1 it
    warps time the way perspective does (fast off the club, slow near the apex); 1 = no warp.
    Also runs vectorized on NumPy arrays in the fits."""
    r = tau / (tau + depth_ratio * (1.0 - tau))
    u: N = 1.0 - (1.0 - r) ** ease  # float ** float is typed Any (it could be complex)
    return u


def head_param(tracer: dict[str, Any], t: float) -> tuple[int, float] | None:
    """Return (segment, u) of the ball head at time t: segment 0 = ascent, 1 = descent.

    None before impact. After landing returns (1, 1.0).
    """
    t_i = tracer["impact_time_s"]
    t_a = tracer["apex_time_s"]
    t_l = tracer["landing_time_s"]
    if t < t_i:
        return None
    if t <= t_a:
        timing = tracer["timing"]
        tau = (t - t_i) / (t_a - t_i)
        return 0, ascent_u(tau, timing["ascent_ease"], timing.get("ascent_depth_ratio", 1.0))
    if t <= t_l:
        tau = (t - t_a) / (t_l - t_a)
        u = tau ** tracer["timing"]["descent_ease"]
        return 1, u
    return 1, 1.0


def head_at(tracer: dict[str, Any], t: float) -> Point | None:
    hp = head_param(tracer, t)
    if hp is None:
        return None
    seg, u = hp
    p = tracer["path"]
    if seg == 0:
        return bezier(u, p["start"], p["c1"], p["apex"])
    return bezier(u, p["apex"], p["c2"], p["landing"])


def sample_path(tracer: dict[str, Any]) -> list[Point]:
    """The full path: 200 ascent samples then descent samples 1..199 (apex not repeated)."""
    p = tracer["path"]
    pts = [bezier(sample_u(i), p["start"], p["c1"], p["apex"]) for i in range(SAMPLES_PER_SEGMENT)]
    pts += [bezier(sample_u(i), p["apex"], p["c2"], p["landing"]) for i in range(1, SAMPLES_PER_SEGMENT)]
    return pts


def _px_dist(a: Point, b: Point, w: float, h: float) -> float:
    return math.hypot((b[0] - a[0]) * w, (b[1] - a[1]) * h)


def _cumulative(points: list[Point], w: float, h: float) -> list[float]:
    cum = [0.0]
    for k in range(1, len(points)):
        cum.append(cum[-1] + _px_dist(points[k - 1], points[k], w, h))
    return cum


def fade_alpha(tracer: dict[str, Any], t: float) -> float:
    """Overall opacity at t (assumes t >= impact). 0 means draw nothing."""
    t_l = tracer["landing_time_s"]
    if t <= t_l:
        return 1.0
    persist = tracer["style"]["persist_s"]
    if persist is None:
        return 1.0
    dt = t - t_l - persist
    if dt <= 0:
        return 1.0
    if dt < FADE_S:
        return float(1.0 - dt / FADE_S)
    return 0.0


def frame_plan(tracer: dict[str, Any], t: float) -> FramePlan | None:
    """Everything a renderer needs at video time t, or None when nothing is drawn."""
    hp = head_param(tracer, t)
    if hp is None:
        return None
    alpha = fade_alpha(tracer, t)
    if alpha <= 0.0:
        return None
    video = tracer["video"]
    w, h = float(video["width"]), float(video["height"])
    full = sample_path(tracer)
    cum = _cumulative(full, w, h)
    total = cum[-1]
    style = tracer["style"]

    if t > tracer["landing_time_s"]:
        points = full
        arcs = cum
        head: Point | None = None
    else:
        seg, u_h = hp
        if seg == 0:
            n = sum(1 for i in range(SAMPLES_PER_SEGMENT) if sample_u(i) < u_h)
        else:
            n = SAMPLES_PER_SEGMENT + sum(1 for i in range(1, SAMPLES_PER_SEGMENT) if sample_u(i) < u_h)
        head = head_at(tracer, t)
        assert head is not None
        points = [*full[:n], head]
        arcs = cum[:n]
        arcs.append((arcs[-1] if arcs else 0.0) + (_px_dist(full[n - 1], head, w, h) if n else 0.0))

    widths = [width_at(style, s, total) for s in arcs]
    return {"points": [list(pt) for pt in points], "widths": widths, "alpha": alpha, "head": head}


def width_at(style: dict[str, Any], s: float, total: float) -> float:
    frac = s / total if total > 0 else 0.0
    return float(style["width"]) * (1.0 - (1.0 - float(style["taper"])) * frac)


def validate_tracer(tracer: Any) -> list[str]:
    """Cross-field rules that JSON Schema cannot express. Returns error messages (empty = valid).

    Run JSON Schema validation first (see ``schema.validate_schema``); this assumes the shape is right.
    """
    errors: list[str] = []
    t_i, t_a, t_l = tracer["impact_time_s"], tracer["apex_time_s"], tracer["landing_time_s"]
    dur = tracer["video"]["duration_s"]
    if not t_i < t_a:
        errors.append("impact_time_s must be before apex_time_s")
    if not t_a < t_l:
        errors.append("apex_time_s must be before landing_time_s")
    if t_l > dur + EPS:
        errors.append("landing_time_s must not exceed video.duration_s")
    p = tracer["path"]
    ay = p["apex"][1]
    if abs(p["c1"][1] - ay) > EPS or abs(p["c2"][1] - ay) > EPS:
        errors.append("path.c1, path.apex and path.c2 must share the same y")
    if p["start"][1] < ay - EPS or p["landing"][1] < ay - EPS:
        errors.append("path.apex must be the highest point (smallest y)")
    return errors
