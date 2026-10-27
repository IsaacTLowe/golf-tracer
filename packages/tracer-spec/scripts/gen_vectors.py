"""Regenerate vectors/vectors.json from the Python implementation.

Run: uv run python packages/tracer-spec/scripts/gen_vectors.py
The TypeScript and Python test suites both check themselves against the output.
Only regenerate when the spec math intentionally changes, and review the diff.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from tracer_spec import frame_plan, head_at, sample_path, validate

ROOT = Path(__file__).resolve().parents[1]

EXAMPLE: dict[str, Any] = {
    "schema_version": 1,
    "video": {"width": 1080, "height": 1920, "fps": 120, "duration_s": 9.4},
    "impact_time_s": 3.21,
    "apex_time_s": 5.40,
    "landing_time_s": 8.10,
    "path": {
        "start": [0.512, 0.781],
        "c1": [0.512, 0.164],
        "apex": [0.538, 0.164],
        "c2": [0.552, 0.164],
        "landing": [0.571, 0.402],
    },
    "timing": {"ascent_ease": 2.0, "descent_ease": 1.3},
    "style": {"color": "#FF3B30", "width": 0.006, "taper": 0.4, "glow": 0.5, "persist_s": 2.0},
    "observed": [{"t": 3.218, "x": 0.512, "y": 0.770, "score": 0.91}],
    "fit": {"method": "image_v1", "residual_px": 2.1, "confidence": 0.82},
}

LANDSCAPE: dict[str, Any] = {
    "schema_version": 1,
    "video": {"width": 1920, "height": 1080, "fps": 60, "duration_s": 6.0},
    "impact_time_s": 1.0,
    "apex_time_s": 2.7,
    "landing_time_s": 5.2,
    "path": {
        "start": [0.45, 0.88],
        "c1": [0.30, 0.10],
        "apex": [0.36, 0.10],
        "c2": [0.62, 0.10],
        "landing": [0.70, 0.55],
    },
    "timing": {"ascent_ease": 3.5, "descent_ease": 0.8},
    "style": {"color": "#FFD60A", "width": 0.01, "taper": 1.0, "glow": 0.0, "persist_s": None},
}

STRAIGHT: dict[str, Any] = {
    "schema_version": 1,
    "video": {"width": 720, "height": 1280, "fps": 30, "duration_s": 4.0},
    "impact_time_s": 0.5,
    "apex_time_s": 1.5,
    "landing_time_s": 4.0,
    "path": {
        "start": [0.5, 0.9],
        "c1": [0.5, 0.2],
        "apex": [0.5, 0.2],
        "c2": [0.5, 0.2],
        "landing": [0.5, 0.35],
    },
    "timing": {"ascent_ease": 1.0, "descent_ease": 1.0},
    "style": {"color": "#FFFFFF", "width": 0.004, "taper": 0.0, "glow": 1.0, "persist_s": 0.0},
}

# Perspective-warped ascent, as the auto tracer fits it for a ball flying away from the camera.
PERSPECTIVE: dict[str, Any] = {
    **copy.deepcopy(EXAMPLE),
    "timing": {"ascent_ease": 0.5, "descent_ease": 1.3, "ascent_depth_ratio": 0.05},
}


def times_for(t: dict[str, Any]) -> list[float]:
    ti, ta, tl = t["impact_time_s"], t["apex_time_s"], t["landing_time_s"]
    p = t["style"]["persist_s"]
    ts = [0.0, ti - 0.001, ti, ti + 0.01, (ti + ta) / 2, ta, ta + 0.001, (ta + tl) / 2, tl]
    if p is None:
        ts += [tl + 0.5]
    else:
        ts += [tl + p * 0.5, tl + p + 0.1, tl + p + 0.25, tl + p + 0.31]
    return [round(x, 6) for x in ts]


def plan_summary(tracer: dict[str, Any], t: float) -> dict[str, Any] | None:
    plan = frame_plan(tracer, t)
    if plan is None:
        return None
    pts, ws = plan["points"], plan["widths"]
    n = len(pts)
    idx = sorted({0, n // 4, n // 2, (3 * n) // 4, n - 1})
    return {
        "n_points": n,
        "alpha": plan["alpha"],
        "head": plan["head"],
        "sample_indices": idx,
        "points": [pts[i] for i in idx],
        "widths": [ws[i] for i in idx],
    }


def invalid_cases() -> list[dict[str, Any]]:
    def variant(name: str, mutate: Any) -> dict[str, Any]:
        bad = copy.deepcopy(EXAMPLE)
        mutate(bad)
        return {"name": name, "tracer": bad}

    return [
        variant("c1_y_mismatch", lambda b: b["path"]["c1"].__setitem__(1, 0.2)),
        variant("apex_before_impact", lambda b: b.__setitem__("apex_time_s", 2.0)),
        variant("landing_after_duration", lambda b: b.__setitem__("landing_time_s", 10.0)),
        variant("apex_not_highest", lambda b: b["path"].__setitem__("start", [0.5, 0.1])),
        variant("bad_color", lambda b: b["style"].__setitem__("color", "red")),
        variant("point_out_of_range", lambda b: b["path"].__setitem__("landing", [1.2, 0.4])),
        variant("missing_timing", lambda b: b.pop("timing")),
        variant("wrong_schema_version", lambda b: b.__setitem__("schema_version", 2)),
        variant("extra_property", lambda b: b.__setitem__("bogus", 1)),
        variant("depth_ratio_zero", lambda b: b["timing"].__setitem__("ascent_depth_ratio", 0.0)),
        variant("depth_ratio_above_one", lambda b: b["timing"].__setitem__("ascent_depth_ratio", 1.5)),
    ]


def main() -> None:
    cases = []
    named = (("example", EXAMPLE), ("landscape", LANDSCAPE), ("straight", STRAIGHT), ("perspective", PERSPECTIVE))
    for name, tracer in named:
        assert not validate(tracer), (name, validate(tracer))
        full = sample_path(tracer)
        cases.append(
            {
                "name": name,
                "tracer": tracer,
                "path_len": len(full),
                "path_every_25": [*full[::25], full[-1]],
                "frames": [
                    {"t": t, "head": head_at(tracer, t), "plan": plan_summary(tracer, t)} for t in times_for(tracer)
                ],
            }
        )
    invalid = invalid_cases()
    for c in invalid:
        assert validate(c["tracer"]), c["name"]
    out = {"tolerance": 1e-6, "cases": cases, "invalid": invalid}
    (ROOT / "vectors" / "vectors.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(cases)} cases, {len(invalid)} invalid cases")


if __name__ == "__main__":
    main()
