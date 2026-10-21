"""Confidence scoring and routing. Thresholds are starting values; tune on the golden set."""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Literal

REVIEW_THRESHOLD = 0.7
CHECK_THRESHOLD = 0.4
FULL_TRACK_S = 0.25  # this much tracked flight earns full credit
MIN_TRACK_POINTS = 4

Route = Literal["review", "review_check", "needs_input"]


@dataclass
class ConfidenceInputs:
    address_score: float  # 0..1
    impact_agreement: float  # 0..1
    tracked_points: int
    fps: float
    residual_px: float | None
    track_quality: float = 1.0  # 0..1: does the track look like a ball leaving (see track_quality)


def score(c: ConfidenceInputs) -> float:
    s_track = min(1.0, c.tracked_points / max(3.0, FULL_TRACK_S * c.fps)) * c.track_quality
    s_fit = math.exp(-(c.residual_px or 50.0) / 6.0)
    conf = 0.2 * min(1.0, c.address_score) + 0.2 * c.impact_agreement + 0.35 * s_track + 0.25 * s_fit
    if c.tracked_points < MIN_TRACK_POINTS:
        conf = min(conf, 0.3)
    if c.track_quality < 0.5:
        conf = min(conf, 0.45)
    return round(max(0.0, min(1.0, conf)), 3)


def route(conf: float, has_tracer: bool) -> Route:
    if not has_tracer or conf < CHECK_THRESHOLD:
        return "needs_input"
    if conf < REVIEW_THRESHOLD:
        return "review_check"
    return "review"


def track_quality(start_y: float, ys: list[float]) -> float:
    """Plausibility of an early-flight track (normalized y, down = larger).

    The ball climbs steadily after impact: most steps move up, and the track rises a clear
    distance from the address position. Junk tracks (shake, golfer edges) wander instead.
    """
    if len(ys) < 2:
        return 0.0
    head = ys[: max(2, int(len(ys) * 0.7))]
    steps = [b - a for a, b in itertools.pairwise(head)]
    upward = sum(1 for d in steps if d <= 0.002) / max(len(steps), 1)
    rise = start_y - min(ys)
    rise_score = min(1.0, max(0.0, rise / 0.12))
    return round(upward * rise_score, 3)
