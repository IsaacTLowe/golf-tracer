"use client";

import type { Point } from "@golf-tracer/tracer-spec";

export interface WizardState {
  step: 1 | 2 | 3;
  impactTime: number | null;
  start: Point | null;
  apex: Point | null;
}

export const WIZARD_START: WizardState = { step: 1, impactTime: null, start: null, apex: null };

const STEPS = {
  1: { title: "Step 1 of 3 · Impact", text: "Scrub to the moment of impact, then tap the ball." },
  2: { title: "Step 2 of 3 · Apex", text: "Tap where the ball peaked (its highest point on screen)." },
  3: { title: "Step 3 of 3 · Landing", text: "Tap where it landed or disappeared." },
} as const;

/** Instruction bar for the three-tap manual tracer. */
export function WizardBar({ state, busy, onBack, onCancel }: { state: WizardState; busy: boolean; onBack: () => void; onCancel: () => void }) {
  const s = STEPS[state.step];
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl bg-amber-400 p-3 text-black" role="status" data-testid="wizard">
      <div className="mr-auto">
        <div className="text-xs font-semibold uppercase tracking-wide">{s.title}</div>
        <div className="text-sm">{busy ? "Building your tracer…" : s.text}</div>
      </div>
      {state.step > 1 && (
        <button type="button" className="tap rounded-lg bg-black/10 px-3 text-sm" onClick={onBack} disabled={busy}>
          Back
        </button>
      )}
      <button type="button" className="tap rounded-lg bg-black/10 px-3 text-sm" onClick={onCancel} disabled={busy}>
        Cancel
      </button>
    </div>
  );
}

/** Dots for the points tapped so far, plus the detected ball as a hint. */
export function WizardMarks({ state, hint }: { state: WizardState; hint: Point | null }) {
  const dot = (p: Point, label: string, cls: string) => (
    <span
      key={label}
      className={`pointer-events-none absolute flex -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-2 border-white ${cls}`}
      style={{ left: `${p[0] * 100}%`, top: `${p[1] * 100}%`, width: 18, height: 18 }}
      aria-label={label}
    />
  );
  return (
    <>
      {state.step === 1 && hint && (
        <span
          className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 animate-pulse rounded-full border-2 border-dashed border-amber-300"
          style={{ left: `${hint[0] * 100}%`, top: `${hint[1] * 100}%`, width: 40, height: 40 }}
          aria-label="Detected ball"
        />
      )}
      {state.start && dot(state.start, "Start", "bg-amber-400")}
      {state.apex && dot(state.apex, "Apex", "bg-sky-400")}
    </>
  );
}
