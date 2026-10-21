"use client";

import type { TracerStyle } from "@golf-tracer/tracer-spec";

export const COLOR_PRESETS: { name: string; value: string }[] = [
  { name: "Red", value: "#FF3B30" },
  { name: "Yellow", value: "#FFD60A" },
  { name: "White", value: "#FFFFFF" },
  { name: "Blue", value: "#0A84FF" },
];

interface Props {
  style: TracerStyle;
  onChange: (patch: Partial<TracerStyle>) => void;
  onSaveDefault?: () => void;
  savingDefault?: boolean;
}

function Slider(props: { label: string; value: number; min: number; max: number; step: number; format: (v: number) => string; onChange: (v: number) => void }) {
  const id = `s-${props.label.replace(/\s/g, "")}`;
  return (
    <label htmlFor={id} className="flex flex-col gap-1 text-sm">
      <span className="flex justify-between">
        <span>{props.label}</span>
        <span className="tabular-nums text-muted">{props.format(props.value)}</span>
      </span>
      <input
        id={id}
        type="range"
        className="tap w-full accent-[var(--accent)]"
        min={props.min}
        max={props.max}
        step={props.step}
        value={props.value}
        onChange={(e) => props.onChange(Number(e.target.value))}
      />
    </label>
  );
}

export function StylePanel({ style, onChange, onSaveDefault, savingDefault }: Props) {
  const persistUntilEnd = style.persist_s === null;
  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="mb-2 text-sm">Colour</div>
        <div className="flex flex-wrap items-center gap-2">
          {COLOR_PRESETS.map((c) => (
            <button
              key={c.value}
              type="button"
              aria-label={c.name}
              aria-pressed={style.color.toUpperCase() === c.value}
              className={`tap rounded-full border-2 ${style.color.toUpperCase() === c.value ? "border-accent" : "border-border"}`}
              onClick={() => onChange({ color: c.value })}
            >
              <span className="m-auto block h-7 w-7 rounded-full border border-black/20" style={{ background: c.value }} />
            </button>
          ))}
          <label className="tap inline-flex items-center gap-2 rounded-lg border border-border px-2 text-sm">
            Custom
            <input
              type="color"
              aria-label="Custom colour"
              value={style.color}
              onChange={(e) => onChange({ color: e.target.value.toUpperCase() })}
              className="h-8 w-10 cursor-pointer bg-transparent"
            />
          </label>
        </div>
      </div>
      <Slider label="Width" value={style.width} min={0.002} max={0.02} step={0.0005} format={(v) => `${(v * 100).toFixed(2)}%`} onChange={(v) => onChange({ width: v })} />
      <Slider label="Glow" value={style.glow} min={0} max={1} step={0.05} format={(v) => v.toFixed(2)} onChange={(v) => onChange({ glow: v })} />
      <Slider label="Taper" value={style.taper} min={0} max={1} step={0.05} format={(v) => v.toFixed(2)} onChange={(v) => onChange({ taper: v })} />
      <div className="flex flex-col gap-2">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="h-5 w-5"
            checked={persistUntilEnd}
            onChange={(e) => onChange({ persist_s: e.target.checked ? null : 2 })}
          />
          Keep the tracer until the end of the clip
        </label>
        {!persistUntilEnd && (
          <Slider
            label="Stay after landing"
            value={style.persist_s ?? 2}
            min={0}
            max={10}
            step={0.5}
            format={(v) => `${v.toFixed(1)} s`}
            onChange={(v) => onChange({ persist_s: v })}
          />
        )}
      </div>
      {onSaveDefault && (
        <button type="button" className="btn" onClick={onSaveDefault} disabled={savingDefault}>
          {savingDefault ? "Saving…" : "Save as my default"}
        </button>
      )}
    </div>
  );
}
