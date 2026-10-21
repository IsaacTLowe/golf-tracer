"use client";

import { drawTracer, type Ctx2D, type Layer, type Point, type Tracer } from "@golf-tracer/tracer-spec";
import { useCallback, useEffect, useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";

import { useVideoClock } from "@/hooks/useVideoClock";

interface Props {
  src: string;
  width: number;
  height: number;
  tracer: Tracer | null;
  showTracer: boolean;
  videoRef: RefObject<HTMLVideoElement | null>;
  onTime?: (t: number) => void;
  /** Taps on the frame, in normalized coordinates (wizard, labelling). */
  onFrameTap?: (p: Point) => void;
  /** Rendered over the frame box (edit handles, markers). */
  children?: ReactNode;
  className?: string;
}

type AnyCanvas = HTMLCanvasElement | OffscreenCanvas;

/** Scratch layers for the renderer, reused across frames. */
function useLayers() {
  const cache = useRef(new Map<string, { canvas: AnyCanvas; ctx: Ctx2D }>());
  return useCallback((w: number, h: number, key: "glow" | "main"): Layer => {
    let entry = cache.current.get(key);
    if (!entry) {
      const canvas: AnyCanvas =
        typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(w, h) : Object.assign(document.createElement("canvas"), { width: w, height: h });
      const ctx = canvas.getContext("2d") as unknown as Ctx2D;
      entry = { canvas, ctx };
      cache.current.set(key, entry);
    }
    if (entry.canvas.width !== w || entry.canvas.height !== h) {
      entry.canvas.width = w;
      entry.canvas.height = h;
    }
    return { ctx: entry.ctx, image: entry.canvas };
  }, []);
}

export function TracerPlayer({ src, width, height, tracer, showTracer, videoRef, onTime, onFrameTap, children, className }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const layers = useLayers();
  const tracerRef = useRef(tracer);
  const showRef = useRef(showTracer);
  const lastT = useRef(0);

  const draw = useCallback(
    (t: number) => {
      lastT.current = t;
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const doc = tracerRef.current;
      if (!doc || !showRef.current) return;
      drawTracer(ctx as unknown as Ctx2D, doc, t, { width: canvas.width, height: canvas.height, layers });
    },
    [layers],
  );

  const time = useVideoClock(videoRef, draw, src);
  useEffect(() => onTime?.(time), [time, onTime]);

  // Keep the canvas at the frame box's size times devicePixelRatio.
  useEffect(() => {
    const box = boxRef.current;
    const canvas = canvasRef.current;
    if (!box || !canvas) return;
    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const r = box.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(r.width * dpr));
      canvas.height = Math.max(1, Math.round(r.height * dpr));
      draw(lastT.current);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(box);
    return () => ro.disconnect();
  }, [draw]);

  // Redraw immediately when the tracer or visibility changes (e.g. while dragging a handle).
  useLayoutEffect(() => {
    tracerRef.current = tracer;
    showRef.current = showTracer;
    draw(videoRef.current?.currentTime ?? lastT.current);
  }, [tracer, showTracer, draw, videoRef]);

  const onClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!onFrameTap) return;
    const r = e.currentTarget.getBoundingClientRect();
    onFrameTap([(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]);
  };

  return (
    <div className={`flex w-full justify-center ${className ?? ""}`}>
      <div
        ref={boxRef}
        className="relative max-h-[70vh] max-w-full overflow-hidden rounded-xl bg-black"
        style={{ aspectRatio: `${width} / ${height}`, height: height >= width ? "70vh" : undefined, width: height >= width ? undefined : "100%" }}
      >
        <video
          ref={videoRef}
          src={src}
          playsInline
          preload="auto"
          crossOrigin="anonymous"
          className="absolute inset-0 h-full w-full object-fill"
        />
        <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden />
        <div
          className={`absolute inset-0 ${onFrameTap ? "cursor-crosshair" : ""}`}
          onClick={onClick}
          data-testid="frame-box"
        >
          {children}
        </div>
      </div>
    </div>
  );
}
