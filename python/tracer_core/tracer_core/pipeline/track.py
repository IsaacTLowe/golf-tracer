"""Early-flight ball tracking after impact.

Candidates: three-frame differencing (the object present in frame k but not in k-1 or k+1),
filtered by size and shape, with large moving regions (the golfer) and their surroundings
masked out. Each candidate is its centroid: for a motion-blurred streak that is the ball at
mid-exposure, which keeps observations consistent from frame to frame (mixing streak ends
with centroids made the track jitter by half a streak).

Association: seed hypotheses from candidates in the first few frames that move up and away
from the address position, score each by how many later frames it predicts with a
constant-velocity model, then extend the best one with a perspective motion model (see
``_perspective_model``) and a gate proportional to the predicted move. The club head is the
main early false positive; it curves sharply around the golfer, so it fails the smooth-path test.
Tracking stops after ``MAX_MISSES`` consecutive misses or when the ball leaves the frame.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

MAX_MISSES = 5
SEED_FRAMES = 4
MAX_TRACK_S = 3.0
PREDICT_POINTS = 12
GATE_PX = 3.0  # association gate: this + GATE_FRACTION × the predicted move since the last hit, widened per miss
GATE_FRACTION = 0.5
GATE_MISS_GROWTH = 0.5


@dataclass
class Blob:
    x: float
    y: float
    area: float
    strength: float


@dataclass
class TrackPoint:
    k: int  # index into the analysed frame list
    x: float
    y: float
    score: float


def motion_blobs(
    prev: np.ndarray,
    cur: np.ndarray,
    nxt: np.ndarray,
    *,
    max_area: float,
    golfer_area: float,
    edge_tol_px: float = 0.0,
) -> list[Blob]:
    """Moving-object candidates in ``cur``. ``edge_tol_px`` > 0 (stabilized clips) tolerates
    that much residual misalignment: the threshold grows with the local image gradient, so
    static edges don't light up while a ball that moved a full diameter still does."""
    d1 = cv2.absdiff(cur, prev)
    d2 = cv2.absdiff(cur, nxt)
    motion = cv2.min(d1, d2)
    noise = float(np.median(d1)) * 1.4826 + 1.0
    thr = max(6.0, 4.0 * noise)
    if edge_tol_px > 0:
        gx = cv2.Sobel(cur, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        gy = cv2.Sobel(cur, cv2.CV_32F, 0, 1, ksize=3) / 8.0
        grad = cv2.dilate(cv2.magnitude(gx, gy), np.ones((3, 3), np.uint8))
        bw = (motion.astype(np.float32) > thr + edge_tol_px * grad).astype(np.uint8)
    else:
        bw = (motion > thr).astype(np.uint8)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(bw, connectivity=8)
    areas = stats[:, cv2.CC_STAT_AREA]
    # Mask big movers (golfer, club shaft) plus a margin around them.
    big = np.isin(labels, np.flatnonzero(areas[1:] > golfer_area) + 1).astype(np.uint8)
    if big.any():
        big = cv2.dilate(big, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    sums = np.bincount(labels.ravel(), weights=motion.ravel(), minlength=n)
    out: list[Blob] = []
    for i in range(1, n):
        area = float(areas[i])
        cx, cy = float(cents[i][0]), float(cents[i][1])
        if area > max_area or big[int(cy), int(cx)]:
            continue
        out.append(Blob(cx, cy, area, float(sums[i]) / area))
    return out


IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
HANDHELD_MIN_SHIFT_PX = 0.35


def frame_motion(frames: list[np.ndarray], first: int) -> list[np.ndarray]:
    """Camera-motion compensation for handheld clips.

    motion[k] is the 2x3 similarity transform mapping frame k-1 onto frame k, from background
    feature tracks (Lucas-Kanade) with RANSAC, so the moving golfer and ball are outliers. When
    the camera is effectively still (median shift below HANDHELD_MIN_SHIFT_PX) everything stays
    identity: resampling a tripod clip would only add noise.
    """
    motion = [IDENTITY.copy() for _ in frames]
    shifts = []
    for k in range(max(1, first), len(frames)):
        a, b = frames[k - 1], frames[k]
        pts = cv2.goodFeaturesToTrack(a, maxCorners=300, qualityLevel=0.01, minDistance=8, blockSize=7)
        if pts is None or len(pts) < 12:
            continue
        nxt, status, _err = cv2.calcOpticalFlowPyrLK(a, b, pts, None, winSize=(21, 21), maxLevel=3)
        good = status.reshape(-1) == 1
        if good.sum() < 12:
            continue
        m, inliers = cv2.estimateAffinePartial2D(
            pts[good], nxt[good], method=cv2.RANSAC, ransacReprojThreshold=1.0, maxIters=500
        )
        if m is None or inliers is None or inliers.sum() < 10:
            continue
        motion[k] = m.astype(np.float32)
        shifts.append(float(np.hypot(m[0, 2], m[1, 2])))
    if not shifts or float(np.median(shifts)) < HANDHELD_MIN_SHIFT_PX:
        return [IDENTITY.copy() for _ in frames]
    return motion


def reference_transforms(motion: list[np.ndarray], ref: int) -> list[np.ndarray]:
    """2x3 transforms taking frame k's pixels to frame ``ref``'s, chaining ``motion`` (k-1 → k)."""
    out: list[np.ndarray] = [IDENTITY.copy() for _ in motion]
    acc = np.eye(3)
    for k in range(ref + 1, len(motion)):
        acc = acc @ np.linalg.inv(np.vstack([motion[k], [0.0, 0.0, 1.0]]))
        out[k] = acc[:2]
    return out


def align(frame: np.ndarray, m: np.ndarray, *, inverse: bool = False) -> np.ndarray:
    """Warp ``frame`` with the similarity ``m`` (or its inverse) onto its neighbour."""
    if np.allclose(m, IDENTITY):
        return frame
    mm = cv2.invertAffineTransform(m) if inverse else m
    out: np.ndarray = cv2.warpAffine(frame, mm, (frame.shape[1], frame.shape[0]), borderMode=cv2.BORDER_REPLICATE)
    return out


def track(
    frames: list[np.ndarray],
    times: list[float],
    start_xy: tuple[float, float],
    ball_r: float,
    impact_k: int,
    *,
    stabilize: bool = True,
) -> tuple[list[TrackPoint], list[list[Blob]]]:
    """Track the ball from ``frames[impact_k]`` onwards. ``times`` are seconds since impact."""
    h, w = frames[0].shape
    max_area = max(60.0, (ball_r * 2.5) ** 2 * 6)
    golfer_area = max(400.0, 0.004 * w * h)
    blobs: list[list[Blob]] = [[] for _ in frames]
    ref = max(0, impact_k - 1)
    motion = frame_motion(frames, ref) if stabilize else [IDENTITY] * len(frames)
    # Candidates are kept in the reference frame's coordinates so camera shake doesn't jitter
    # the track (the predictor would read the jitter as the ball turning).
    to_ref = reference_transforms(motion, ref)
    for k in range(max(1, impact_k), len(frames) - 1):
        prev = align(frames[k - 1], motion[k])
        nxt = align(frames[k + 1], motion[k + 1], inverse=True)
        stabilized = not (np.allclose(motion[k], IDENTITY) and np.allclose(motion[k + 1], IDENTITY))
        blobs[k] = motion_blobs(
            prev, frames[k], nxt, max_area=max_area, golfer_area=golfer_area, edge_tol_px=0.6 if stabilized else 0.0
        )
        m = to_ref[k]
        for b in blobs[k]:
            b.x, b.y = (float(m[0, 0] * b.x + m[0, 1] * b.y + m[0, 2]), float(m[1, 0] * b.x + m[1, 1] * b.y + m[1, 2]))

    s = np.array(start_xy, dtype=np.float64)
    first = impact_k + (1 if times[impact_k] <= 0 else 0)
    last = len(frames) - 1
    best: list[TrackPoint] | None = None
    best_score = 0.0
    # Seed from pairs of early candidates that leave the address position upward on a
    # consistent heading and don't accelerate; then let the predictor extend each seed a few
    # frames and keep the seed that explains the most frames.
    for k1 in range(first, min(first + SEED_FRAMES, last)):
        for b1 in blobs[k1]:
            p1 = np.array([b1.x, b1.y])
            d1 = p1 - s
            if d1[1] > -max(1.5, 0.5 * ball_r) or abs(d1[0]) > abs(d1[1]) * 1.2:
                continue
            frames_from_impact = max(times[k1] / max(times[1] - times[0], 1e-6), 1.0) if len(times) > 1 else 1.0
            v1 = float(np.linalg.norm(d1)) / frames_from_impact
            if v1 < max(2.0, ball_r):  # the ball leaves fast; slow movers are golfer/club jitter
                continue
            for k2 in range(k1 + 1, min(k1 + 3, last)):
                for b2 in blobs[k2]:
                    p2 = np.array([b2.x, b2.y])
                    d2 = p2 - p1
                    n2 = float(np.linalg.norm(d2))
                    if d2[1] >= 0 or n2 < 0.5:
                        continue
                    cosang = float(d1 @ d2) / (float(np.linalg.norm(d1)) * n2)
                    if cosang < math.cos(math.radians(35)):
                        continue
                    if not 0.25 * v1 <= n2 / (k2 - k1) <= v1 * 1.5 + 2.0:
                        continue
                    pts = [TrackPoint(k1, float(p1[0]), float(p1[1]), b1.strength),
                           TrackPoint(k2, float(p2[0]), float(p2[1]), b2.strength)]  # fmt: skip
                    pts = _extend(pts, blobs, times, w, h, stop=min(k2 + 10, last), max_misses=2)
                    travel = float(np.hypot(pts[-1].x - pts[0].x, pts[-1].y - pts[0].y))
                    score = len(pts) - 0.1 * (k1 - first) + 0.001 * travel
                    if score > best_score:
                        best, best_score = pts, score
    if best is None or best_score < 3:
        return [], blobs

    max_k = min(last, impact_k + int(MAX_TRACK_S / max(times[1] - times[0], 1e-3))) if len(times) > 1 else last
    return _extend(best, blobs, times, w, h, stop=max_k, max_misses=MAX_MISSES), blobs


def _extend(
    pts: list[TrackPoint], blobs: list[list[Blob]], times: list[float], w: int, h: int, *, stop: int, max_misses: int
) -> list[TrackPoint]:
    out = list(pts)
    k = out[-1].k + 1
    misses = 0
    while k < stop:
        pred = _predict(out, times, k)
        if not (0 <= pred[0] < w and 0 <= pred[1] < h):
            break
        # The predicted move since the last hit already grows over missed frames.
        step = float(np.hypot(pred[0] - out[-1].x, pred[1] - out[-1].y))
        cand = _nearest(blobs[k], pred, GATE_PX + GATE_FRACTION * step * (1.0 + GATE_MISS_GROWTH * misses))
        if cand is None:
            misses += 1
            if misses >= max_misses:
                break
        else:
            misses = 0
            out.append(TrackPoint(k, cand[0], cand[1], cand[2]))
        k += 1
    return out


def _nearest(cands: list[Blob], pred: np.ndarray, gate: float) -> tuple[float, float, float] | None:
    best = None
    best_d = gate
    for b in cands:
        d = float(np.hypot(b.x - pred[0], b.y - pred[1]))
        if d < best_d:
            best, best_d = (b.x, b.y, b.strength), d
    return best


def _predict(pts: list[TrackPoint], times: list[float], k: int) -> np.ndarray:
    recent = pts[-PREDICT_POINTS:]
    ts = np.array([times[p.k] for p in recent])
    xs = np.array([p.x for p in recent])
    ys = np.array([p.y for p in recent])
    model = (_perspective_model(ts, xs, ys) if len(ts) >= 4 else None) or _poly_model(ts, xs, ys)
    return model(times[k])


def _poly_model(ts: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> Callable[[float], np.ndarray]:
    deg = 2 if len(ts) >= 4 else 1
    cx, cy = np.polyfit(ts, xs, deg), np.polyfit(ts, ys, deg)
    return lambda t: np.array([np.polyval(cx, t), np.polyval(cy, t)])


def _perspective_model(ts: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> Callable[[float], np.ndarray] | None:
    """Screen motion of a ball flying away from the camera. Over a short window its 3D velocity
    is about constant, and a pinhole camera then sees x = (a0 + a1·t) / (1 + c·t) and
    y = (b0 + b1·t) / (1 + c·t). A polynomial can't follow the sharp perspective slow-down and
    overshoots into a turn-back; this model is monotone, so it never predicts one. (A gravity
    term was tried: on a few noisy points it predicts the ball stopping and falling.)
    Solved linearly via x·(1 + c·t) = a0 + a1·t; None unless c ≥ 0 (receding)."""
    n = len(ts)
    zero, one = np.zeros(n), np.ones(n)
    x_rows = np.column_stack([one, ts, zero, zero, -ts * xs])
    y_rows = np.column_stack([zero, zero, one, ts, -ts * ys])
    a0, a1, b0, b1, c = np.linalg.lstsq(np.vstack([x_rows, y_rows]), np.concatenate([xs, ys]), rcond=None)[0]
    if c < 0:
        return None
    return lambda t: np.array([a0 + a1 * t, b0 + b1 * t]) / (1 + c * t)
