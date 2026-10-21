"use client";

// Golden-dataset labelling mode (open a clip with ?label=1). Click the ball frame by frame;
// export labels as JSON for datasets/golden/labels/. Matches the format the evaluator reads.

import type { Point } from "@golf-tracer/tracer-spec";
import { useState } from "react";

export type LabelTarget = "address" | "ball" | "landing";

export interface Labels {
  address: { x: number; y: number; r: number } | null;
  impact_frame: number | null;
  track: Record<number, { x: number; y: number }>;
  landing: { x: number; y: number } | null;
}

export const EMPTY_LABELS: Labels = { address: null, impact_frame: null, track: {}, landing: null };

export function useLabels() {
  const [labels, setLabels] = useState<Labels>(EMPTY_LABELS);
  const [target, setTarget] = useState<LabelTarget>("address");
  const tap = (p: Point, frame: number, defaultR: number) => {
    setLabels((l) => {
      if (target === "address") return { ...l, address: { x: p[0], y: p[1], r: l.address?.r ?? defaultR } };
      if (target === "landing") return { ...l, landing: { x: p[0], y: p[1] } };
      return { ...l, track: { ...l.track, [frame]: { x: p[0], y: p[1] } } };
    });
  };
  return { labels, setLabels, target, setTarget, tap };
}

interface Props {
  labels: Labels;
  target: LabelTarget;
  frame: number;
  fps: number;
  meta: { width: number; height: number; duration_s: number; filename: string };
  onTarget: (t: LabelTarget) => void;
  onImpact: () => void;
  onClearFrame: () => void;
}

export function LabelPanel({ labels, target, frame, fps, meta, onTarget, onImpact, onClearFrame }: Props) {
  const exportJson = () => {
    const doc = {
      clip: meta.filename,
      width: meta.width,
      height: meta.height,
      fps,
      frame_count: Math.round(meta.duration_s * fps),
      address: labels.address,
      impact_frame: labels.impact_frame,
      impact_time_s: labels.impact_frame != null ? labels.impact_frame / fps : null,
      track: Object.entries(labels.track)
        .map(([f, p]) => ({ frame: Number(f), t: Number(f) / fps, x: p.x, y: p.y }))
        .sort((a, b) => a.frame - b.frame),
      landing: labels.landing,
    };
    const blob = new Blob([JSON.stringify(doc, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${meta.filename.replace(/\.[^.]+$/, "")}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <div className="card flex flex-col gap-3 p-3 text-sm" data-testid="label-tool">
      <div className="font-semibold">Labelling · frame {frame}</div>
      <div className="flex flex-wrap gap-1">
        {(["address", "ball", "landing"] as LabelTarget[]).map((t) => (
          <button key={t} type="button" className={`tap rounded-lg px-3 ${target === t ? "bg-accent text-accent-fg" : "border border-border"}`} onClick={() => onTarget(t)}>
            {t === "address" ? "Ball at address" : t === "ball" ? "Ball in flight" : "Landing"}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn" onClick={onImpact}>
          Impact = this frame
        </button>
        <button type="button" className="btn" onClick={onClearFrame}>
          Clear this frame
        </button>
        <button type="button" className="btn-primary" onClick={exportJson}>
          Export labels
        </button>
      </div>
      <div className="text-xs text-muted">
        Address {labels.address ? "✓" : "–"} · impact {labels.impact_frame ?? "–"} · {Object.keys(labels.track).length} ball points · landing{" "}
        {labels.landing ? "✓" : "–"}. In “Ball in flight” mode each click advances one frame.
      </div>
    </div>
  );
}

export function LabelMarks({ labels, frame }: { labels: Labels; frame: number }) {
  const pts: { p: { x: number; y: number }; cls: string; key: string }[] = [];
  if (labels.address) pts.push({ p: labels.address, cls: "border-amber-300", key: "a" });
  if (labels.landing) pts.push({ p: labels.landing, cls: "border-rose-400", key: "l" });
  for (const [f, p] of Object.entries(labels.track)) {
    pts.push({ p, cls: Number(f) === frame ? "border-lime-300 bg-lime-300/50" : "border-lime-300/60", key: `t${f}` });
  }
  return (
    <>
      {pts.map(({ p, cls, key }) => (
        <span
          key={key}
          className={`pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-2 ${cls}`}
          style={{ left: `${p.x * 100}%`, top: `${p.y * 100}%`, width: 10, height: 10 }}
        />
      ))}
    </>
  );
}
