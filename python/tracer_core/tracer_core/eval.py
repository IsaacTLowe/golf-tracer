"""Tracer-quality evaluation: ``python -m tracer_core.eval``.

Runs the auto-tracing pipeline over a labelled clip set and reports the metrics from the
spec (address detection, impact error, track recall/error, landing error, confidence
calibration, route rate), plus tracer error: how far the drawn tracer head is from the ball
while it is visible. With ``--baseline`` it fails (exit 1) when any metric regresses
beyond the baseline's tolerance; ``--record`` writes the current numbers as the baseline.

Sets:
  synthetic  generated clips (tracer_core.synth.standard_set), cached under --cache-dir
  golden     real clips: labels in datasets/golden/labels/*.json, videos (normalized MP4s
             named <label stem>.mp4) in --clips-dir, or downloaded from the private
             storage bucket given by --bucket (service-role key from the environment)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tracer_spec.spec import head_at

from . import media, synth
from .pipeline import auto_trace

REPO = Path(__file__).resolve().parents[3]
GOLDEN = REPO / "datasets" / "golden"

GOOD_LANDING_ERROR = 0.06  # fraction of frame height

# metric -> (direction, default absolute tolerance)
METRICS: dict[str, tuple[str, float]] = {
    "address_detection_rate": ("higher", 0.05),
    "impact_within_2_frames": ("higher", 0.05),
    "impact_error_frames": ("lower", 0.5),
    "track_recall": ("higher", 0.05),
    "track_error_px": ("lower", 1.5),
    "landing_error": ("lower", 0.02),
    "tracer_error": ("lower", 0.005),
    "confidence_separation": ("higher", 0.1),
    "review_rate": ("higher", 0.1),
}


def evaluate_clip(video: Path, labels: dict[str, Any], pipeline: str = "v2") -> dict[str, Any]:
    p = media.probe(video)
    audio = media.extract_audio(video)
    meta = {"width": p.width, "height": p.height, "fps": p.nominal_fps, "duration_s": p.duration_s}
    r = auto_trace(video, meta, audio=audio, version=pipeline)
    W, H, fps = p.width, p.height, p.nominal_fps
    a = labels["address"]
    out: dict[str, Any] = {"route": r.route, "confidence": r.confidence, "reason": r.reason, "timings": r.timings}

    if r.ball_xy is not None:
        d = math.hypot((r.ball_xy[0] - a["x"]) * W, (r.ball_xy[1] - a["y"]) * H)
        out["address_error_px"] = d
        out["address_ok"] = d <= 2 * max(a["r"] * H, 1.5)
    else:
        out["address_ok"] = False
    if r.impact_time_s is not None:
        out["impact_error_frames"] = abs(r.impact_time_s * fps - labels["impact_frame"])
    track = {int(q["frame"]): q for q in labels.get("track", [])}
    got = {round(t * fps): (x, y) for t, x, y in r.observed}
    matched = [f for f in track if f in got]
    out["track_labelled"] = len(track)
    out["track_matched"] = len(matched)
    if matched:
        errs = [math.hypot((got[f][0] - track[f]["x"]) * W, (got[f][1] - track[f]["y"]) * H) for f in matched]
        out["track_error_px"] = sum(errs) / len(errs)
    landing = labels.get("landing")
    if r.tracer is not None and landing:
        lx, ly = r.tracer["path"]["landing"]
        out["landing_error"] = math.hypot((lx - landing["x"]) * W, (ly - landing["y"]) * H) / H
    # What the viewer sees: is the drawn tracer head on the ball while the ball is visible?
    heads = [(head_at(r.tracer, f / fps), q) for f, q in track.items()] if r.tracer is not None else []
    head_errs = [math.hypot((hd[0] - q["x"]) * W, (hd[1] - q["y"]) * H) / H for hd, q in heads if hd is not None]
    if head_errs:
        out["tracer_error"] = sum(head_errs) / len(head_errs)
    # "Good" judges the result the user gets (the tracer), not how much of the flight was
    # tracked: a short but accurate track that yields a correct tracer is a good clip.
    out["good"] = bool(
        out.get("address_ok")
        and out.get("impact_error_frames", 99) <= 2
        and out.get("landing_error", 1.0) <= GOOD_LANDING_ERROR
    )
    return out


def summarize(results: dict[str, dict[str, Any]]) -> dict[str, float]:
    rs = list(results.values())
    n = max(len(rs), 1)

    def mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else float("nan")

    labelled = sum(r["track_labelled"] for r in rs)
    impact = [r["impact_error_frames"] for r in rs if "impact_error_frames" in r]
    good = [r["confidence"] for r in rs if r["good"]]
    bad = [r["confidence"] for r in rs if not r["good"]]
    return {
        "clips": float(len(rs)),
        "address_detection_rate": sum(1 for r in rs if r["address_ok"]) / n,
        "impact_within_2_frames": sum(1 for e in impact if e <= 2) / n,
        "impact_error_frames": mean(impact),
        "track_recall": sum(r["track_matched"] for r in rs) / max(labelled, 1),
        "track_error_px": mean([r["track_error_px"] for r in rs if "track_error_px" in r]),
        "landing_error": mean([r["landing_error"] for r in rs if "landing_error" in r]),
        "tracer_error": mean([r["tracer_error"] for r in rs if "tracer_error" in r]),
        # Calibration: good clips should score higher than bad ones (1.0 when there are no bad clips).
        "confidence_separation": (mean(good) - mean(bad)) if good and bad else 1.0,
        "review_rate": sum(1 for r in rs if r["route"] != "needs_input") / n,
        "check_rate": sum(1 for r in rs if r["route"] == "review_check") / n,
        "needs_input_rate": sum(1 for r in rs if r["route"] == "needs_input") / n,
    }


def compare(current: dict[str, float], baseline: dict[str, Any]) -> list[str]:
    failures = []
    base = baseline["metrics"]
    tol = baseline.get("tolerance", {})
    for name, (direction, default_tol) in METRICS.items():
        if name not in base or name not in current or math.isnan(current[name]) or math.isnan(base[name]):
            continue
        t = float(tol.get(name, default_tol))
        cur, ref = current[name], base[name]
        if (direction == "higher" and cur < ref - t) or (direction == "lower" and cur > ref + t):
            failures.append(f"{name}: {cur:.4f} vs baseline {ref:.4f} (tolerance {t})")
    return failures


def synthetic_clips(cache: Path) -> list[tuple[Path, dict[str, Any]]]:
    out = []
    for name, params in synth.standard_set():
        key_src = json.dumps({**asdict(params), "_labels": synth.LABEL_VERSION}, sort_keys=True)
        key = hashlib.sha1(key_src.encode()).hexdigest()[:10]
        stem = f"{name}_{key}"
        video, lab = cache / f"{stem}.mp4", cache / f"{stem}.json"
        if not (video.exists() and lab.exists()):
            print(f"generating {stem} ...", file=sys.stderr)
            synth.generate(cache, stem, params)
        out.append((video, json.loads(lab.read_text(encoding="utf-8"))))
    return out


def golden_clips(clips_dir: Path | None, bucket: str | None, cache: Path) -> list[tuple[Path, dict[str, Any]]]:
    labels = sorted((GOLDEN / "labels").glob("*.json"))
    out = []
    store = None
    if bucket:
        from .storage import from_settings

        store = from_settings()
    for lab_path in labels:
        lab = json.loads(lab_path.read_text(encoding="utf-8"))
        name = lab.get("clip") or f"{lab_path.stem}.mp4"
        video = (clips_dir / name) if clips_dir else (cache / name)
        if not video.exists() and store is not None and bucket:
            store.download_to(bucket, name, video)
        if not video.exists():
            print(f"skipping {lab_path.stem}: video not found", file=sys.stderr)
            continue
        out.append((video, lab))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tracer_core.eval", description=__doc__.split("\n")[0])
    ap.add_argument("--set", choices=["synthetic", "golden"], default="synthetic")
    ap.add_argument("--clips-dir", type=Path)
    ap.add_argument("--bucket", help="storage bucket holding golden clips (service role)")
    ap.add_argument("--cache-dir", type=Path, default=GOLDEN / ".cache")
    ap.add_argument("--baseline", type=Path, help="compare against this baseline JSON")
    ap.add_argument("--record", action="store_true", help="write the results as the new baseline")
    ap.add_argument("--report", type=Path, help="write per-clip results JSON here")
    ap.add_argument(
        "--pipeline",
        choices=["v1", "v2"],
        default="v2",
        help="v1: image-space fit only, no stabilization; v2: current",
    )
    args = ap.parse_args(argv)

    cache = args.cache_dir / args.set
    cache.mkdir(parents=True, exist_ok=True)
    clips = synthetic_clips(cache) if args.set == "synthetic" else golden_clips(args.clips_dir, args.bucket, cache)
    if not clips:
        print("no clips to evaluate", file=sys.stderr)
        return 2
    results = {}
    for video, lab in clips:
        res = evaluate_clip(video, lab, args.pipeline)
        results[video.stem] = res
        print(
            f"{video.stem:60s} {res['route']:12s} conf={res['confidence']:.2f} "
            f"addr={'ok' if res['address_ok'] else 'MISS'} "
            f"impact_err={res.get('impact_error_frames', float('nan')):.1f} "
            f"track={res['track_matched']}/{res['track_labelled']} err={res.get('track_error_px', float('nan')):.1f}px "
            f"landing={res.get('landing_error', float('nan')):.3f} tracer={res.get('tracer_error', float('nan')):.3f}",
            file=sys.stderr,
        )
    summary = summarize(results)
    print(json.dumps(summary, indent=2))
    if args.report:
        args.report.write_text(
            json.dumps({"summary": summary, "clips": results}, indent=1, default=str), encoding="utf-8"
        )
    if args.record:
        target = args.baseline or (GOLDEN / f"baseline_{args.set}.json")
        target.write_text(
            json.dumps(
                {
                    "set": args.set,
                    "pipeline": args.pipeline,
                    "metrics": summary,
                    "tolerance": {k: v[1] for k, v in METRICS.items()},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"recorded baseline to {target}", file=sys.stderr)
        return 0
    if args.baseline:
        failures = compare(summary, json.loads(args.baseline.read_text(encoding="utf-8")))
        if failures:
            print("REGRESSIONS:\n  " + "\n  ".join(failures), file=sys.stderr)
            return 1
        print("no regressions against baseline", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
