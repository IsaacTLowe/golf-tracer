"""Image-space trajectory model shared by manual tracing and the auto-tracing fit.

The tracer path is two quadratic Béziers meeting at the apex (see Tracer spec). This module
turns a handful of user- or tracker-supplied facts (where the ball started, peaked and
landed; when it was struck) into a complete, valid tracer document.
"""

from __future__ import annotations

import copy
from typing import Any

from tracer_spec import DEFAULT_STYLE, DEFAULT_TIMING, SCHEMA_VERSION, validate

# Tunable defaults (record changes in docs/DECISIONS.md).
DEFAULT_FLIGHT_S = 5.0
APEX_TIME_FRACTION = 0.4  # apex (highest screen point) time as a fraction of the flight
C1_FRACTION = 0.25  # how far along start→apex (in x) the launch control sits
C2_FRACTION = 0.55  # how far along apex→landing (in x) the descent control sits
CURVE_SCALE = 0.12  # x shift of c1 per unit of `curve`


class ModelError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _clamp01(v: float) -> float:
    return min(1.0, max(0.0, float(v)))


def flight_times(
    impact_time_s: float, duration_s: float, fps: float, *, flight_s: float = DEFAULT_FLIGHT_S
) -> tuple[float, float]:
    """(apex_time_s, landing_time_s) for a flight starting at impact, clipped to the video."""
    min_flight = 3.0 / fps
    remaining = duration_s - impact_time_s
    if remaining < min_flight:
        raise ModelError("impact_too_late", "Impact is too close to the end of the clip to draw a tracer.")
    flight = min(flight_s, remaining)
    landing = impact_time_s + flight
    apex = impact_time_s + APEX_TIME_FRACTION * flight
    return apex, landing


def controls_for(
    start: list[float], apex: list[float], landing: list[float], curve: float = 0.0
) -> tuple[list[float], list[float]]:
    """Control points c1/c2 for a launch shape; both sit on the apex line."""
    ay = apex[1]
    c1x = start[0] + (apex[0] - start[0]) * C1_FRACTION - curve * CURVE_SCALE
    c2x = apex[0] + (landing[0] - apex[0]) * C2_FRACTION
    return [_clamp01(c1x), ay], [_clamp01(c2x), ay]


def build_tracer(
    *,
    video: dict[str, Any],
    impact_time_s: float,
    start: list[float],
    apex: list[float],
    landing: list[float],
    curve: float = 0.0,
    style: dict[str, Any] | None = None,
    apex_time_s: float | None = None,
    landing_time_s: float | None = None,
    timing: dict[str, float] | None = None,
    observed: list[dict[str, float]] | None = None,
    fit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start = [_clamp01(start[0]), _clamp01(start[1])]
    landing = [_clamp01(landing[0]), _clamp01(landing[1])]
    # The apex is by definition the highest point on screen.
    apex = [_clamp01(apex[0]), min(_clamp01(apex[1]), start[1], landing[1])]
    if start[1] - apex[1] < 0.01:
        raise ModelError("apex_too_low", "The apex must be clearly above the ball's starting point.")

    if apex_time_s is None or landing_time_s is None:
        a_t, l_t = flight_times(impact_time_s, video["duration_s"], video["fps"])
        apex_time_s = a_t if apex_time_s is None else apex_time_s
        landing_time_s = l_t if landing_time_s is None else landing_time_s
    landing_time_s = min(landing_time_s, video["duration_s"])

    c1, c2 = controls_for(start, apex, landing, curve)
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "video": {
            "width": int(video["width"]),
            "height": int(video["height"]),
            "fps": float(video["fps"]),
            "duration_s": float(video["duration_s"]),
        },
        "impact_time_s": float(impact_time_s),
        "apex_time_s": float(apex_time_s),
        "landing_time_s": float(landing_time_s),
        "path": {"start": start, "c1": c1, "apex": apex, "c2": c2, "landing": landing},
        "timing": dict(timing or DEFAULT_TIMING),
        "style": resolve_style(style),
    }
    if observed is not None:
        doc["observed"] = observed
    if fit is not None:
        doc["fit"] = fit
    errors = validate(doc)
    if errors:
        raise ModelError("invalid_tracer", "; ".join(errors))
    return doc


def resolve_style(style: dict[str, Any] | None) -> dict[str, Any]:
    merged = copy.deepcopy(DEFAULT_STYLE)
    if style:
        merged.update({k: v for k, v in style.items() if k in DEFAULT_STYLE})
    return merged


def default_landing_y(horizon_y: float | None) -> float:
    """Where an unseen landing goes: just below the horizon, or a default fraction of height."""
    return min(0.95, (horizon_y if horizon_y is not None else 0.42) + 0.02)
