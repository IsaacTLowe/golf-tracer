from __future__ import annotations

import pytest
from tracer_core.model import ModelError, build_tracer, flight_times
from tracer_spec import validate

VIDEO = {"width": 1080, "height": 1920, "fps": 60.0, "duration_s": 8.0}


def test_build_tracer_produces_valid_doc() -> None:
    doc = build_tracer(video=VIDEO, impact_time_s=2.0, start=[0.5, 0.8], apex=[0.52, 0.2], landing=[0.56, 0.42])
    assert validate(doc) == []
    assert doc["landing_time_s"] == pytest.approx(7.0)
    assert doc["impact_time_s"] < doc["apex_time_s"] < doc["landing_time_s"]


def test_flight_is_clipped_to_the_video() -> None:
    apex, landing = flight_times(6.5, 8.0, 60.0)
    assert landing == pytest.approx(8.0)
    assert 6.5 < apex < landing


def test_impact_at_the_very_end_is_rejected() -> None:
    with pytest.raises(ModelError) as e:
        flight_times(7.99, 8.0, 60.0)
    assert e.value.code == "impact_too_late"


def test_apex_is_forced_to_be_highest() -> None:
    doc = build_tracer(video=VIDEO, impact_time_s=1.0, start=[0.5, 0.8], apex=[0.5, 0.6], landing=[0.5, 0.4])
    assert doc["path"]["apex"][1] == 0.4
    assert validate(doc) == []


def test_curve_shifts_launch_control() -> None:
    left = build_tracer(video=VIDEO, impact_time_s=1.0, start=[0.5, 0.8], apex=[0.5, 0.2], landing=[0.5, 0.4], curve=-1)
    right = build_tracer(video=VIDEO, impact_time_s=1.0, start=[0.5, 0.8], apex=[0.5, 0.2], landing=[0.5, 0.4], curve=1)
    assert left["path"]["c1"][0] > right["path"]["c1"][0]


def test_style_defaults_and_overrides() -> None:
    doc = build_tracer(
        video=VIDEO, impact_time_s=1.0, start=[0.5, 0.8], apex=[0.5, 0.2], landing=[0.5, 0.4],
        style={"color": "#FFFFFF", "unknown": 1},
    )  # fmt: skip
    assert doc["style"]["color"] == "#FFFFFF"
    assert doc["style"]["width"] == 0.006
    assert "unknown" not in doc["style"]
