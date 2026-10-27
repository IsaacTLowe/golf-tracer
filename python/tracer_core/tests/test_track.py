from __future__ import annotations

import importlib

import numpy as np
from tracer_core import synth

track = importlib.import_module("tracer_core.pipeline.track")


def _screen_flight(fps: float, n: int) -> tuple[list[float], np.ndarray]:
    """A drive seen from behind, in pixels, sampled from impact."""
    p = synth.SynthParams(fps=int(fps))
    cam, flight = synth.Camera(p), synth.simulate_flight(p)
    times = [i / fps for i in range(n)]
    uv = np.array([cam.project(flight[np.searchsorted(flight[:, 0], t), 1:])[:2] for t in times])
    return times, uv


def test_perspective_prediction_follows_a_receding_ball() -> None:
    times, uv = _screen_flight(60.0, 30)
    seen = [track.TrackPoint(k, float(x), float(y), 1.0) for k, (x, y) in enumerate(uv[2:14], start=2)]
    for k in (16, 20, 24):
        err = float(np.hypot(*(track._predict(seen, times, k) - uv[k])))
        moved = float(np.hypot(*(uv[k] - uv[13])))
        assert err < 0.1 * moved, (k, err, moved)  # well inside the association gate


def test_perspective_beats_a_quadratic_over_a_gap() -> None:
    times, uv = _screen_flight(60.0, 30)
    ts, xs, ys = np.array(times[2:10]), uv[2:10, 0], uv[2:10, 1]
    persp = track._perspective_model(ts, xs, ys)
    assert persp is not None
    quad = track._poly_model(ts, xs, ys)
    target = uv[18]
    assert np.hypot(*(persp(times[18]) - target)) < 0.25 * np.hypot(*(quad(times[18]) - target))


def test_approaching_motion_falls_back_to_polynomial() -> None:
    ts = np.linspace(0.0, 0.2, 8)
    # Screen motion that speeds up (an object approaching the camera) means c < 0.
    xs, ys = 100 + 50 * ts / (1 - 2 * ts), 500 - 80 * ts / (1 - 2 * ts)
    assert track._perspective_model(ts, xs, ys) is None
