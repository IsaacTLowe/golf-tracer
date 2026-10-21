"""Find the ball at address: a small, roughly round, light blob in the lower part of the
frame that stays put across many pre-impact frames.

Candidates come from a classical detector (white top-hat + connected components). An ONNX
object detector (COCO "sports ball") can add candidates when a model file is configured;
see ``detector.py``. Candidates are clustered across sampled frames and ranked by how
persistently they appear; the final choice among the top clusters is made in ``impact.py``
by checking which one actually disappears when the ball is struck.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

SEARCH_TOP = 0.40  # only the part of the frame below this fraction of height
MIN_R_FRAC = 0.0022  # ball radius limits as a fraction of frame height (detection scale)
MAX_R_FRAC = 0.013


@dataclass
class Candidate:
    x: float
    y: float
    r: float
    contrast: float
    fill: float


@dataclass
class AddressCluster:
    x: float
    y: float
    r: float
    contrast: float
    fill: float
    hits: int
    samples: int
    members: list[Candidate] = field(default_factory=list)

    @property
    def persistence(self) -> float:
        return self.hits / max(self.samples, 1)

    height: int = 0

    @property
    def score(self) -> float:
        contrast = min(1.0, self.contrast / 40.0)
        # The ball sits in the lower part of the frame, near the golfer's feet.
        low = min(1.0, max(0.0, (self.y / self.height - 0.4) / 0.4)) if self.height else 1.0
        return float(
            self.persistence * (0.5 + 0.5 * contrast) * (0.6 + 0.4 * min(1.0, self.fill / 0.7)) * (0.6 + 0.4 * low)
        )


def radius_limits(height: int) -> tuple[float, float]:
    return max(1.0, MIN_R_FRAC * height), max(3.0, MAX_R_FRAC * height)


def ball_candidates(
    gray: np.ndarray,
    near: tuple[float, float] | None = None,
    near_r: float | None = None,
    horizon_y: float | None = None,
) -> list[Candidate]:
    h, _w = gray.shape
    r_min, r_max = radius_limits(h)
    k = int(2 * round(r_max * 2) + 1)
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    # A ball on the ground is always below the horizon (the camera is above the ground).
    top = SEARCH_TOP if horizon_y is None else max(SEARCH_TOP, horizon_y + 0.03)
    y0 = int(h * min(top, 0.85))
    region = tophat[y0:]
    thr = max(10.0, float(region.mean() + 4.0 * region.std()))
    bw = (region > thr).astype(np.uint8)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(bw, connectivity=8)
    out: list[Candidate] = []
    a_min, a_max = np.pi * (r_min * 0.6) ** 2, np.pi * (r_max * 1.4) ** 2
    for i in range(1, n):
        _x, _y, bw_, bh_, area = stats[i]
        if not a_min <= area <= a_max:
            continue
        aspect = bw_ / max(bh_, 1)
        if not 0.5 <= aspect <= 2.0:
            continue
        fill = area / float(bw_ * bh_)
        if fill < 0.45:
            continue
        cx, cy = float(cents[i][0]), float(cents[i][1]) + y0
        if near is not None:
            lim = 4.0 * (near_r or r_max)
            if abs(cx - near[0]) > lim or abs(cy - near[1]) > lim:
                continue
        mask = labels == i
        contrast = float(region[mask].mean())
        r = float(np.sqrt(area / np.pi))
        out.append(Candidate(cx, cy, r, contrast, float(fill)))
    return out


def cluster_candidates(per_frame: list[list[Candidate]], radius_px: float = 3.0) -> list[AddressCluster]:
    clusters: list[AddressCluster] = []
    samples = len(per_frame)
    for cands in per_frame:
        used: set[int] = set()
        for c in cands:
            best, best_d = None, radius_px + max(1.0, c.r * 0.5)
            for ci, cl in enumerate(clusters):
                if ci in used:
                    continue
                d = float(np.hypot(cl.x - c.x, cl.y - c.y))
                if d < best_d:
                    best, best_d = ci, d
            if best is None:
                clusters.append(AddressCluster(c.x, c.y, c.r, c.contrast, c.fill, 1, samples, [c]))
                used.add(len(clusters) - 1)
            else:
                cl = clusters[best]
                cl.members.append(c)
                cl.hits += 1
                m = cl.members
                cl.x = float(np.median([q.x for q in m]))
                cl.y = float(np.median([q.y for q in m]))
                cl.r = float(np.median([q.r for q in m]))
                cl.contrast = float(np.mean([q.contrast for q in m]))
                cl.fill = float(np.mean([q.fill for q in m]))
                used.add(best)
    for cl in clusters:
        cl.samples = samples
    clusters.sort(key=lambda c: c.score, reverse=True)
    return clusters


def find_address(
    frames: list[np.ndarray],
    *,
    hint: tuple[float, float] | None = None,
    horizon_y: float | None = None,
    top_k: int = 8,
) -> list[AddressCluster]:
    """Ranked stationary ball candidates from sampled pre-impact frames (detection scale)."""
    if not frames:
        return []
    per_frame = [ball_candidates(f, near=hint, horizon_y=None if hint else horizon_y) for f in frames]
    clusters = cluster_candidates(per_frame)
    for c in clusters:
        c.height = frames[0].shape[0]
    clusters.sort(key=lambda c: c.score, reverse=True)
    clusters = [c for c in clusters if c.persistence >= 0.3]
    if hint is not None and not clusters:
        # Trust the user's tap even if the detector found nothing there.
        h = frames[0].shape[0]
        r = 0.006 * h
        return [AddressCluster(hint[0], hint[1], r, 0.0, 0.0, 0, len(frames))]
    return clusters[:top_k]
