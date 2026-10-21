"use client";

import type { TimeMarker, Tracer } from "@golf-tracer/tracer-spec";
import { useRef } from "react";

const MARKERS: { key: TimeMarker; field: "impact_time_s" | "apex_time_s" | "landing_time_s"; label: string; color: string }[] = [
  { key: "impact", field: "impact_time_s", label: "Impact", color: "bg-amber-400" },
  { key: "apex", field: "apex_time_s", label: "Apex", color: "bg-sky-400" },
  { key: "landing", field: "landing_time_s", label: "Landing", color: "bg-rose-400" },
];

interface Props {
  duration: number;
  time: number;
  tracer: Tracer | null;
  editable: boolean;
  onSeek: (t: number) => void;
  onMarkerDragStart?: () => void;
  onMarkerMove?: (marker: TimeMarker, t: number) => void;
  onMarkerDragEnd?: () => void;
}

export function formatTime(t: number): string {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(2).padStart(5, "0")}`;
}

/** Scrub bar with impact/apex/landing markers; markers drag in edit mode. */
export function Timeline({ duration, time, tracer, editable, onSeek, onMarkerDragStart, onMarkerMove, onMarkerDragEnd }: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const pct = (t: number) => `${Math.min(100, Math.max(0, (t / Math.max(duration, 1e-6)) * 100))}%`;
  const timeAt = (clientX: number) => {
    const r = trackRef.current!.getBoundingClientRect();
    return Math.min(duration, Math.max(0, ((clientX - r.left) / r.width) * duration));
  };

  return (
    <div className="w-full select-none">
      <div
        ref={trackRef}
        className="relative h-11 touch-none cursor-pointer"
        role="slider"
        aria-label="Seek"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={time}
        tabIndex={-1}
        onPointerDown={(e) => {
          (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
          onSeek(timeAt(e.clientX));
        }}
        onPointerMove={(e) => {
          if ((e.currentTarget as HTMLElement).hasPointerCapture(e.pointerId)) onSeek(timeAt(e.clientX));
        }}
      >
        <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-border" />
        <div className="absolute left-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-accent" style={{ width: pct(time) }} />
        <div
          className="absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-accent shadow"
          style={{ left: pct(time) }}
        />
        {tracer &&
          MARKERS.map((m) => (
            <button
              key={m.key}
              type="button"
              aria-label={`${m.label} at ${formatTime(tracer[m.field])}${editable ? ", drag to adjust" : ""}`}
              title={m.label}
              data-marker={m.key}
              className={`absolute top-0 flex h-11 w-11 -translate-x-1/2 items-start justify-center ${editable ? "cursor-ew-resize" : ""}`}
              style={{ left: pct(tracer[m.field]) }}
              onPointerDown={(e) => {
                e.stopPropagation();
                if (!editable) {
                  onSeek(tracer[m.field]);
                  return;
                }
                (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
                onMarkerDragStart?.();
              }}
              onPointerMove={(e) => {
                if (editable && (e.currentTarget as HTMLElement).hasPointerCapture(e.pointerId)) {
                  const t = timeAt(e.clientX);
                  onMarkerMove?.(m.key, t);
                  onSeek(t);
                }
              }}
              onPointerUp={(e) => {
                if (editable && (e.currentTarget as HTMLElement).hasPointerCapture(e.pointerId)) {
                  (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
                  onMarkerDragEnd?.();
                }
              }}
            >
              <span className={`mt-0.5 block h-3 w-1.5 rounded-sm ${m.color} ${editable ? "h-5 w-2" : ""}`} />
            </button>
          ))}
      </div>
      <div className="flex justify-between text-xs tabular-nums text-muted">
        <span>{formatTime(time)}</span>
        {tracer && (
          <span className="hidden gap-3 sm:flex">
            {MARKERS.map((m) => (
              <span key={m.key} className="inline-flex items-center gap-1">
                <span className={`inline-block h-2 w-2 rounded-sm ${m.color}`} />
                {m.label} {formatTime(tracer[m.field])}
              </span>
            ))}
          </span>
        )}
        <span>{formatTime(duration)}</span>
      </div>
    </div>
  );
}
