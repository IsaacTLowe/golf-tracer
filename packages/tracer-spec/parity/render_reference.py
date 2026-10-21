"""Render reference frames with the Python (server) renderer for the visual parity check.

Writes, for each case: a background PNG, the expected PNG (background + tracer drawn by
tracer_spec.render) and an index.json describing the tracer and time. The Playwright parity
test draws the same tracer onto the same background with the TypeScript canvas renderer in a
headless browser and compares pixels.

Usage: uv run python packages/tracer-spec/parity/render_reference.py <out_dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from tracer_spec.render import draw_tracer

ROOT = Path(__file__).resolve().parents[1]


def background(w: int, h: int) -> np.ndarray:
    """A deterministic sky-to-grass gradient with some texture (BGR)."""
    ys = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    xs = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    sky = ys < 0.55
    b = np.where(sky, 230 - 60 * ys, 60 + 30 * xs)
    g = np.where(sky, 190 - 40 * ys, 120 + 20 * np.sin(xs * 40) * np.cos(ys * 30))
    r = np.where(sky, 140 + 80 * ys, 50 + 10 * xs)
    return np.clip(np.stack([b, g, r], axis=-1), 0, 255).astype(np.uint8)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "parity-out")
    out.mkdir(parents=True, exist_ok=True)
    vectors = json.loads((ROOT / "vectors" / "vectors.json").read_text(encoding="utf-8"))
    cases = []
    for case in vectors["cases"]:
        tracer = case["tracer"]
        # Render at a reduced size of the clip (keeps CI fast; widths scale with height anyway).
        w, h = tracer["video"]["width"] // 2, tracer["video"]["height"] // 2
        bg = background(w, h)
        times = [
            (tracer["impact_time_s"] + tracer["apex_time_s"]) / 2,
            (tracer["apex_time_s"] + tracer["landing_time_s"]) / 2,
            tracer["landing_time_s"] + 0.1,
        ]
        for i, t in enumerate(times):
            name = f"{case['name']}_{i}"
            cv2.imwrite(str(out / f"{name}_bg.png"), bg)
            cv2.imwrite(str(out / f"{name}_expected.png"), draw_tracer(bg, tracer, t))
            cases.append({"name": name, "width": w, "height": h, "t": t, "tracer": tracer})
    (out / "index.json").write_text(json.dumps({"cases": cases}, indent=1), encoding="utf-8")
    print(f"wrote {len(cases)} parity cases to {out}")


if __name__ == "__main__":
    main()
