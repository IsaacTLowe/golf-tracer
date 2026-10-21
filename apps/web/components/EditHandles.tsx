"use client";

import type { Handle, Point, Tracer } from "@golf-tracer/tracer-spec";
import { useRef } from "react";

const LABELS: Record<Handle, string> = {
  start: "Start",
  apex: "Apex",
  landing: "Landing",
  c1: "Launch shape",
  c2: "Descent shape",
};

interface Props {
  doc: Tracer;
  advanced: boolean;
  onDragStart: () => void;
  onMove: (handle: Handle, p: Point) => void;
  onDragEnd: () => void;
}

/** Draggable handles over the frame box (44 px touch targets). */
export function EditHandles({ doc, advanced, onDragStart, onMove, onDragEnd }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const handles: Handle[] = advanced ? ["start", "c1", "apex", "c2", "landing"] : ["start", "apex", "landing"];

  const toPoint = (e: React.PointerEvent): Point => {
    const r = boxRef.current!.getBoundingClientRect();
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
  };

  return (
    <div ref={boxRef} className="absolute inset-0">
      {advanced && (
        <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none">
          <polyline
            points={[doc.path.start, doc.path.c1, doc.path.apex, doc.path.c2, doc.path.landing].map((p) => p.join(",")).join(" ")}
            fill="none"
            stroke="white"
            strokeOpacity={0.6}
            strokeDasharray="0.01 0.01"
            strokeWidth={0.003}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      )}
      {handles.map((h) => {
        const [x, y] = doc.path[h];
        const control = h === "c1" || h === "c2";
        return (
          <button
            key={h}
            type="button"
            aria-label={`Drag ${LABELS[h]}`}
            title={LABELS[h]}
            data-handle={h}
            className="tap absolute flex -translate-x-1/2 -translate-y-1/2 touch-none items-center justify-center rounded-full"
            style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: 44, height: 44 }}
            onPointerDown={(e) => {
              e.stopPropagation();
              (e.target as HTMLElement).setPointerCapture(e.pointerId);
              onDragStart();
            }}
            onPointerMove={(e) => {
              if ((e.target as HTMLElement).hasPointerCapture(e.pointerId)) onMove(h, toPoint(e));
            }}
            onPointerUp={(e) => {
              (e.target as HTMLElement).releasePointerCapture(e.pointerId);
              onDragEnd();
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <span
              className={`block rounded-full border-2 border-white shadow-[0_0_0_2px_rgba(0,0,0,0.5)] ${
                control ? "h-3.5 w-3.5 bg-sky-400" : "h-5 w-5 bg-white/30"
              }`}
            />
          </button>
        );
      })}
    </div>
  );
}
