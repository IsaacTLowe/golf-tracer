import json
from pathlib import Path

import numpy as np
from tracer_spec.render import draw_tracer, stroke_mask

VECTORS = json.loads((Path(__file__).resolve().parents[2] / "vectors" / "vectors.json").read_text(encoding="utf-8"))
EXAMPLE = VECTORS["cases"][0]["tracer"]


def test_nothing_drawn_before_impact() -> None:
    frame = np.full((192, 108, 3), 40, np.uint8)
    out = draw_tracer(frame, EXAMPLE, 1.0)
    assert np.array_equal(out, frame)


def test_full_path_drawn_after_landing_in_color() -> None:
    frame = np.zeros((1920, 1080, 3), np.uint8)
    out = draw_tracer(frame, EXAMPLE, EXAMPLE["landing_time_s"] + 0.5)
    # A pixel on the stroke is fully covered in the style colour (BGR).
    sx, sy = EXAMPLE["path"]["start"]
    px = out[int(sy * 1920) - 20, int(sx * 1080)]
    assert tuple(int(v) for v in px) == (0x30, 0x3B, 0xFF)
    assert out[50, 50].sum() == 0


def test_fade_reduces_intensity() -> None:
    frame = np.zeros((1920, 1080, 3), np.uint8)
    t_full = EXAMPLE["landing_time_s"] + 1.0
    t_fade = EXAMPLE["landing_time_s"] + EXAMPLE["style"]["persist_s"] + 0.15
    full = draw_tracer(frame, EXAMPLE, t_full).astype(int).sum()
    faded = draw_tracer(frame, EXAMPLE, t_fade).astype(int).sum()
    assert 0 < faded < full


def test_stroke_mask_width() -> None:
    pts = np.array([[10.0, 50.0], [90.0, 50.0]])
    mask = stroke_mask(pts, np.array([10.0, 10.0]), (100, 100))
    assert abs(float(mask[:, 50].sum()) - 10.0) < 0.6


def test_rasterizer_matches_stateless_renderer() -> None:
    from tracer_spec.render import Rasterizer

    for case in VECTORS["cases"]:
        tracer = case["tracer"]
        w, h = tracer["video"]["width"] // 4, tracer["video"]["height"] // 4
        rng = np.random.default_rng(0)
        base = rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8)
        rast = Rasterizer(tracer, w, h)
        fps = 30.0
        n = int(tracer["video"]["duration_s"] * fps)
        for i in [*list(range(0, n, 3)), 5, 100]:  # includes a backwards seek
            t = i / fps
            a = rast.draw(base, t)
            b = draw_tracer(base, tracer, t)
            assert int(np.abs(a.astype(int) - b.astype(int)).max()) <= 1, (case["name"], t)
