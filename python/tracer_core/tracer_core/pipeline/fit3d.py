"""3D ball-flight fit (method ``physics_v1``, milestone 6).

Models the shot physically (launch speed, launch angle, azimuth, backspin lift and side force,
quadratic drag) and projects it through a pinhole camera standing behind the golfer, then fits
the parameters to the tracked early flight by least squares. The physical model carries the
part of the flight the tracker never sees, so apex and landing extrapolate plausibly.

Camera: focal length from a phone-typical field of view (refined within a prior), pitch from
the detected horizon, distance to the ball from its apparent radius at address (refined), and
height/lateral offset solved so the ball projects where it was found at address.

The fitted flight is converted into a tracer document (two Béziers + eases) by fitting the
spec's curve to the projected trajectory, so renderers stay unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import least_squares, minimize_scalar

from ..model import build_tracer

BALL_RADIUS_M = 0.02135
G = 9.81
K_DRAG = 0.0055  # 0.5 * rho * Cd * A / m  (1/m)
DT = 0.01
MAX_FLIGHT_S = 12.0
F_PRIOR = 0.8  # focal length / long edge (phone main camera, ~65 deg across the long edge)
IMPACT_SLACK_FRAMES = 1.5


@dataclass
class Camera:
    f: float
    cx: float
    cy: float
    pos: np.ndarray  # world position (x lateral, y downrange, z up)
    pitch: float

    def project(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        d = pts - self.pos
        c, s = math.cos(self.pitch), math.sin(self.pitch)
        fwd = d[:, 1] * c + d[:, 2] * s
        up = -d[:, 1] * s + d[:, 2] * c
        fwd_safe = np.maximum(fwd, 1e-3)
        return self.cx + self.f * d[:, 0] / fwd_safe, self.cy - self.f * up / fwd_safe, fwd


def camera_for(f: float, W: float, H: float, ball_px: tuple[float, float], dist: float, pitch: float) -> Camera:
    """Place the camera so the ball (world origin) projects at ``ball_px`` from ``dist`` metres."""
    cx, cy = W / 2, H / 2
    fwd = dist
    up = fwd * (cy - ball_px[1]) / f
    lat = fwd * (ball_px[0] - cx) / f
    c, s = math.cos(pitch), math.sin(pitch)
    dy = fwd * c - up * s
    dz = fwd * s + up * c
    return Camera(f=f, cx=cx, cy=cy, pos=np.array([-lat, -dy, -dz]), pitch=pitch)


def simulate(v0: float, launch: float, azimuth: float, lift: float, side: float, t_max: float) -> np.ndarray:
    """(t, x, y, z) samples every DT from impact until landing or t_max (RK2 / midpoint)."""
    la, az = math.radians(launch), math.radians(azimuth)
    v = np.array([v0 * math.cos(la) * math.sin(az), v0 * math.cos(la) * math.cos(az), v0 * math.sin(la)])
    p = np.zeros(3)

    def acc(vel: np.ndarray) -> np.ndarray:
        speed = math.sqrt(float(vel @ vel))
        hn = math.hypot(vel[0], vel[1]) or 1e-9
        up = np.array([-vel[0] * vel[2] / hn, -vel[1] * vel[2] / hn, hn])
        up /= math.sqrt(float(up @ up)) or 1.0
        sd = np.array([vel[1], -vel[0], 0.0]) / hn
        a: np.ndarray = -K_DRAG * speed * vel + lift * speed * speed * up + side * speed * sd + np.array([0.0, 0.0, -G])
        return a

    out = [(0.0, 0.0, 0.0, 0.0)]
    t = 0.0
    while t < t_max:
        a1 = acc(v)
        vm = v + a1 * (DT / 2)
        p = p + vm * DT
        v = v + acc(vm) * DT
        t += DT
        out.append((t, p[0], p[1], p[2]))
        if p[2] < 0 and t > 0.2:
            break
    return np.asarray(out)


@dataclass
class PhysicsFit:
    tracer: dict[str, Any]
    residual_px: float
    params: dict[str, float]


def fit_physics(
    observed: list[tuple[float, float, float]],
    start: tuple[float, float],
    ball_r: float | None,
    impact_time_s: float,
    video: dict[str, Any],
    horizon_y: float | None,
    style: dict[str, Any] | None = None,
) -> PhysicsFit | None:
    """``observed``: (t, x, y) normalized; ``ball_r``: ball radius at address as a fraction of height."""
    if len(observed) < 5:
        return None
    W, H = float(video["width"]), float(video["height"])
    fps = float(video["fps"])
    long_edge = max(W, H)
    obs = np.asarray(observed, dtype=np.float64)
    t_rel = obs[:, 0] - impact_time_s
    ox, oy = obs[:, 1] * W, obs[:, 2] * H
    ball_px = (start[0] * W, start[1] * H)
    r_px = (ball_r or 0.0055) * H
    f0 = F_PRIOR * long_edge
    d0 = f0 * BALL_RADIUS_M / max(r_px, 0.5)
    pitch0 = math.atan(((H / 2) - horizon_y * H) / f0) if horizon_y is not None else 0.0
    t_max = float(t_rel.max()) + 0.05

    def unpack(th: np.ndarray) -> tuple[Camera, np.ndarray, float]:
        v0, launch, az, lift, side, log_d, f_scale, dt0 = th
        f = f0 * f_scale
        pitch = math.atan(((H / 2) - horizon_y * H) / f) if horizon_y is not None else pitch0
        cam = camera_for(f, W, H, ball_px, d0 * math.exp(log_d), pitch)
        traj = simulate(v0, launch, az, lift, side, t_max + abs(dt0))
        return cam, traj, dt0

    def residuals(th: np.ndarray) -> np.ndarray:
        cam, traj, dt0 = unpack(th)
        u, v, _ = cam.project(traj[:, 1:])
        ts = traj[:, 0]
        tq = np.clip(t_rel - dt0, 0.0, ts[-1])
        pu = np.interp(tq, ts, u)
        pv = np.interp(tq, ts, v)
        r = np.concatenate([pu - ox, pv - oy])
        priors = [
            (th[3] - 0.004) / 0.003 * 2.0,  # typical backspin lift
            th[4] / 0.01 * 2.0,  # little side force unless the data says so
            th[5] / 0.3 * 2.0,  # distance from the apparent ball size
            (th[6] - 1.0) / 0.15 * 2.0,  # focal length near the phone prior
        ]
        return np.concatenate([r, priors])

    slack = IMPACT_SLACK_FRAMES / fps
    lo = np.array([12.0, 2.0, -25.0, 0.0, -0.03, -0.9, 0.7, -slack])
    hi = np.array([90.0, 45.0, 25.0, 0.012, 0.03, 0.9, 1.4, slack])
    # Initial azimuth from the early lateral drift.
    drift = (ox[min(len(ox) - 1, 5)] - ball_px[0]) / max(abs(oy[min(len(oy) - 1, 5)] - ball_px[1]), 1.0)
    best = None
    for v0, launch in ((55.0, 12.0), (40.0, 20.0), (70.0, 10.0), (30.0, 30.0)):
        x0 = np.array([v0, launch, float(np.clip(drift * 10, -20, 20)), 0.004, 0.0, 0.0, 1.0, 0.0])
        try:
            res = least_squares(
                residuals, x0, bounds=(lo, hi), loss="soft_l1", f_scale=4.0, max_nfev=200, x_scale="jac"
            )
        except ValueError, FloatingPointError:
            continue
        if best is None or res.cost < best.cost:
            best = res
    if best is None:
        return None
    n = len(obs)
    residual_px = float(np.sqrt(np.mean(best.fun[:n] ** 2 + best.fun[n : 2 * n] ** 2)))
    v0, launch, az, lift, side, log_d, _f_scale, dt0 = (float(x) for x in best.x)
    cam, _, _ = unpack(best.x)
    full = simulate(v0, launch, az, lift, side, MAX_FLIGHT_S)
    u, v, _depth = cam.project(full[:, 1:])
    impact = max(0.0, impact_time_s + dt0)
    tracer = _to_tracer(full[:, 0], u / W, v / H, impact, video, style)
    if tracer is None:
        return None
    tracer["observed"] = [{"t": float(t), "x": float(x), "y": float(y)} for t, x, y in observed]
    tracer["fit"] = {
        "method": "physics_v1",
        "residual_px": round(residual_px, 3),
        "speed_mps": round(v0, 2),
        "launch_deg": round(launch, 2),
        "azimuth_deg": round(az, 2),
        "carry_m": round(float(full[-1, 2]), 1),
    }
    return PhysicsFit(
        tracer=tracer,
        residual_px=residual_px,
        params={
            "speed": v0,
            "launch": launch,
            "azimuth": az,
            "lift": lift,
            "side": side,
            "distance_m": d0 * math.exp(log_d),
        },
    )


def _bezier(u: np.ndarray, p0: np.ndarray, c: np.ndarray, p1: np.ndarray) -> np.ndarray:
    u = u[:, None]
    out: np.ndarray = (1 - u) ** 2 * p0 + 2 * (1 - u) * u * c + u**2 * p1
    return out


def _to_tracer(
    ts: np.ndarray, xs: np.ndarray, ys: np.ndarray, impact: float, video: dict[str, Any], style: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Fit the spec's two-Bézier tracer to a projected trajectory (normalized coords)."""
    dur = float(video["duration_s"])
    fps = float(video["fps"])
    abs_t = impact + ts
    keep = abs_t <= dur
    if keep.sum() < 4:
        return None
    ts, xs, ys = ts[keep], np.clip(xs[keep], 0.0, 1.0), np.clip(ys[keep], 0.0, 1.0)
    ia = int(np.argmin(ys))
    if ia < 2 or ia >= len(ts) - 2:
        return None  # screen apex not inside the clip; let the image-space fit handle it
    start = np.array([xs[0], ys[0]])
    apex = np.array([xs[ia], ys[ia]])
    land = np.array([xs[-1], ys[-1]])
    if land[1] - apex[1] < 0.01:
        land[1] = apex[1] + 0.01
    t_a, t_l = float(ts[ia]), float(ts[-1])
    if t_l - t_a < 2.0 / fps:
        return None

    asc = np.stack([xs[: ia + 1], ys[: ia + 1]], axis=1)
    desc = np.stack([xs[ia:], ys[ia:]], axis=1)
    uu = np.linspace(0, 1, 300)

    def shape_cost(cx: float, p0: np.ndarray, p1: np.ndarray, pts: np.ndarray) -> float:
        curve = _bezier(uu, p0, np.array([cx, apex[1]]), p1)
        d = ((pts[:, None, :] - curve[None, :, :]) ** 2).sum(axis=2).min(axis=1)
        return float(d.mean())

    c1x = float(minimize_scalar(lambda c: shape_cost(c, start, apex, asc), bounds=(0, 1), method="bounded").x)
    c2x = float(minimize_scalar(lambda c: shape_cost(c, apex, land, desc), bounds=(0, 1), method="bounded").x)

    # Eases: match where the head is over time.
    def ease_cost(p: float, seg_t: np.ndarray, seg_y: np.ndarray, y0: float, y1: float, ascent: bool) -> float:
        tau = (seg_t - seg_t[0]) / (seg_t[-1] - seg_t[0])
        u = 1 - (1 - tau) ** p if ascent else tau**p
        # y along a Bézier whose control shares the apex y is monotone in u
        y = (1 - u) ** 2 * y0 + 2 * (1 - u) * u * (apex[1]) + u**2 * y1
        return float(((y - seg_y) ** 2).mean())

    def asc_cost(p: float) -> float:
        return ease_cost(p, ts[: ia + 1], ys[: ia + 1], start[1], apex[1], True)

    def desc_cost(p: float) -> float:
        return ease_cost(p, ts[ia:], ys[ia:], apex[1], land[1], False)

    pa = float(minimize_scalar(asc_cost, bounds=(1.0, 20.0), method="bounded").x)
    pd = float(minimize_scalar(desc_cost, bounds=(0.3, 20.0), method="bounded").x)
    doc = build_tracer(
        video=video,
        impact_time_s=impact,
        start=[float(start[0]), float(start[1])],
        apex=[float(apex[0]), float(apex[1])],
        landing=[float(land[0]), float(land[1])],
        style=style,
        apex_time_s=impact + t_a,
        landing_time_s=min(impact + t_l, dur),
        timing={"ascent_ease": round(pa, 4), "descent_ease": round(pd, 4)},
    )
    doc["path"]["c1"] = [c1x, doc["path"]["apex"][1]]
    doc["path"]["c2"] = [c2x, doc["path"]["apex"][1]]
    return doc
