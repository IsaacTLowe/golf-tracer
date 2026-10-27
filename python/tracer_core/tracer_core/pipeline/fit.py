"""Image-space trajectory fit (method ``image_v1``).

Fits the tracer's ascent segment directly to the tracked points: with the perspective-warped
time ``r = τ / (τ + k·(1 − τ))`` and the ascent ease p, the head's y follows
``y = s_y + Δ·(1 − (1 − r)^(2p))``; p and the depth ratio k are fitted and x follows the
Bézier through c1, so the early rise and its deceleration pin down the apex height and time
when enough of the flight is visible. Weak priors (typical flight time, horizon) carry the rest. The
descent isn't usually visible, so the landing goes just below the estimated horizon,
continuing the observed lateral drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import least_squares
from tracer_spec import ascent_u

from ..model import C2_FRACTION, DEFAULT_FLIGHT_S, build_tracer, default_landing_y

MIN_EASE = 0.2
MAX_EASE = 20.0  # schema maximum
DEPTH_RATIO_MIN = 0.005
DESCENT_EASE = 1.3
IMPACT_SLACK_FRAMES = 1.5
TIMING_WEIGHT = 0.15
# Seen from behind, the ball reaches its highest *screen* point about a third of the way
# through the flight, then sinks slowly toward the horizon (perspective). Tunable.
SCREEN_APEX_FRACTION = 0.32
DESCENT_TIME_RATIO = (1 - SCREEN_APEX_FRACTION) / SCREEN_APEX_FRACTION
LANDING_DRIFT = 0.35  # landing continues this fraction of the start→apex lateral drift
PRIOR_T_APEX_S = SCREEN_APEX_FRACTION * DEFAULT_FLIGHT_S


@dataclass
class FitResult:
    tracer: dict[str, Any]
    residual_px: float
    n_points: int


def _path(theta: np.ndarray, start: np.ndarray, u: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    dy, _ta, ax, c1x = theta[:4]
    ay = start[1] + dy
    x = (1 - u) ** 2 * start[0] + 2 * (1 - u) * u * c1x + u**2 * ax
    y = (1 - u) ** 2 * start[1] + 2 * (1 - u) * u * ay + u**2 * ay
    return x, y


def _ascent(theta: np.ndarray, start: np.ndarray, tau: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return _path(theta, start, ascent_u(np.clip(tau, 0.0, 1.0), theta[4], theta[6]))


def fit_image_v1(
    observed: list[tuple[float, float, float]],
    start: tuple[float, float],
    impact_time_s: float,
    video: dict[str, Any],
    horizon_y: float | None,
    style: dict[str, Any] | None = None,
) -> FitResult | None:
    """``observed``: (t, x, y) in seconds and normalized coords, excluding the address point."""
    if len(observed) < 3:
        return None
    W, H = float(video["width"]), float(video["height"])
    obs = np.asarray(observed, dtype=np.float64)
    t_rel = obs[:, 0] - impact_time_s
    s = np.asarray(start, dtype=np.float64)
    top_obs = float(obs[:, 2].min())
    hz = horizon_y if horizon_y is not None else 0.45

    u_dense = np.linspace(0.0, 1.0, 600)

    def residuals(theta: np.ndarray) -> np.ndarray:
        tau = (t_rel - theta[5]) / theta[1]
        x, y = _ascent(theta, s, tau)
        # Path residual: distance from each observation to the nearest point on the curve.
        # Timing residual (down-weighted): where the head is at each observation's time. The
        # line itself must follow the ball; timing is pinned down less well by early flight.
        cx, cy = _path(theta, s, u_dense)
        dx = (cx[None, :] - obs[:, 1:2]) * W
        dy_ = (cy[None, :] - obs[:, 2:3]) * H
        nearest = np.argmin(dx * dx + dy_ * dy_, axis=1)
        rows = np.arange(len(obs))
        r = np.concatenate(
            [
                dx[rows, nearest],
                dy_[rows, nearest],
                TIMING_WEIGHT * (x - obs[:, 1]) * W,
                TIMING_WEIGHT * (y - obs[:, 2]) * H,
            ]
        )
        # Weak priors (pixels-equivalent): typical apex time, apex above the horizon, mild launch shape.
        prior_t = 15.0 * (theta[1] - PRIOR_T_APEX_S) / PRIOR_T_APEX_S
        apex_y = s[1] + theta[0]
        prior_h = 0.0 if apex_y < hz - 0.02 else (apex_y - (hz - 0.02)) * H * 0.5
        prior_c = 0.5 * (theta[3] - (s[0] + (theta[2] - s[0]) * 0.25)) * W
        return np.concatenate([r, [prior_t, prior_h, prior_c]])

    # Early flight only pins down ease / apex-time, so the apex-time prior decides the split.
    # The last parameter shifts the start time by up to IMPACT_SLACK_FRAMES to absorb a
    # one-frame impact error, which otherwise dominates the fit (the ball moves fastest then).
    fps = float(video["fps"])
    slack = IMPACT_SLACK_FRAMES / fps
    lo = np.array([-(s[1] - 0.01), 0.25, 0.0, 0.0, MIN_EASE, -slack, DEPTH_RATIO_MIN])
    hi = np.array([-(s[1] - top_obs) * 0.98 + 1e-3 if top_obs < s[1] else -0.01, 8.0, 1.0, 1.0, MAX_EASE, slack, 1.0])
    hi[0] = max(hi[0], lo[0] + 1e-3)
    best = None
    lat = float(obs[-1, 1] - s[0])
    for ta0, ease0, depth0 in ((1.5, 0.5, 0.05), (2.5, 0.5, 0.1), (2.5, 6.0, 1.0), (3.5, 8.0, 1.0)):
        for frac in (1.05, 1.4):
            dy0 = float(np.clip((top_obs - s[1]) * frac, lo[0] + 1e-3, hi[0] - 1e-3))
            x0 = np.array(
                [dy0, ta0, np.clip(s[0] + lat * 1.2, 0, 1), np.clip(s[0] + lat * 0.3, 0, 1), ease0, 0.0, depth0]
            )
            x0 = np.clip(x0, lo + 1e-6, hi - 1e-6)
            try:
                res = least_squares(residuals, x0, bounds=(lo, hi), loss="soft_l1", f_scale=3.0, max_nfev=400)
            except ValueError:
                continue
            if best is None or res.cost < best.cost:
                best = res
    if best is None:
        return None
    dy, ta, ax, c1x, ease, dt0, depth_ratio = (float(v) for v in best.x)
    impact_time_s = max(0.0, impact_time_s + dt0)
    n = len(obs)
    res = best.fun
    residual_px = float(np.sqrt(np.mean(res[:n] ** 2 + res[n : 2 * n] ** 2)))
    timing_px = float(np.sqrt(np.mean(res[2 * n : 3 * n] ** 2 + res[3 * n : 4 * n] ** 2))) / TIMING_WEIGHT

    apex = [ax, float(s[1] + dy)]
    landing_y = max(default_landing_y(horizon_y), apex[1] + 0.03)
    landing_x = float(np.clip(ax + (ax - s[0]) * LANDING_DRIFT, 0, 1))
    dur = float(video["duration_s"])
    apex_t = impact_time_s + ta
    landing_t = apex_t + DESCENT_TIME_RATIO * ta
    if landing_t > dur:
        landing_t = dur
        apex_t = min(apex_t, dur - 2.0 / fps)
    observed_doc = [{"t": float(t), "x": float(x), "y": float(y)} for t, x, y in observed]
    tracer = build_tracer(
        video=video,
        impact_time_s=impact_time_s,
        start=[float(s[0]), float(s[1])],
        apex=apex,
        landing=[landing_x, landing_y],
        style=style,
        apex_time_s=apex_t,
        landing_time_s=landing_t,
        timing={
            "ascent_ease": round(ease, 4),
            "descent_ease": DESCENT_EASE,
            "ascent_depth_ratio": round(depth_ratio, 4),
        },
        observed=observed_doc,
        fit={"method": "image_v1", "residual_px": round(residual_px, 3), "timing_residual_px": round(timing_px, 3)},
    )
    # The fitted c1 replaces the default launch shape; c2 keeps the model default.
    tracer["path"]["c1"] = [float(np.clip(c1x, 0, 1)), apex[1]]
    tracer["path"]["c2"] = [float(np.clip(ax + (landing_x - ax) * C2_FRACTION, 0, 1)), apex[1]]
    return FitResult(tracer=tracer, residual_px=residual_px, n_points=n)
