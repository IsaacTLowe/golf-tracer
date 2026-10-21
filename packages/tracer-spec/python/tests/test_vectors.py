import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tracer_spec import frame_plan, head_at, sample_path, validate

VECTORS = json.loads((Path(__file__).resolve().parents[2] / "vectors" / "vectors.json").read_text(encoding="utf-8"))
TOL = VECTORS["tolerance"]


def close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return bool(np.allclose(np.asarray(a, dtype=float), np.asarray(b, dtype=float), atol=TOL, rtol=0))


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["name"])
def test_vectors(case: dict[str, Any]) -> None:
    tracer = case["tracer"]
    assert validate(tracer) == []
    full = sample_path(tracer)
    assert len(full) == case["path_len"]
    assert close([*full[::25], full[-1]], case["path_every_25"])
    for frame in case["frames"]:
        t = frame["t"]
        assert close(head_at(tracer, t), frame["head"]), t
        plan = frame_plan(tracer, t)
        exp = frame["plan"]
        if exp is None:
            assert plan is None, t
            continue
        assert plan is not None, t
        assert len(plan["points"]) == exp["n_points"], t
        assert abs(plan["alpha"] - exp["alpha"]) <= TOL
        assert close(plan["head"], exp["head"])
        assert close([plan["points"][i] for i in exp["sample_indices"]], exp["points"])
        assert close([plan["widths"][i] for i in exp["sample_indices"]], exp["widths"])


@pytest.mark.parametrize("case", VECTORS["invalid"], ids=lambda c: c["name"])
def test_invalid(case: dict[str, Any]) -> None:
    assert validate(case["tracer"]) != []
