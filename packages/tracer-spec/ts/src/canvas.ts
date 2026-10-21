// Canvas 2D renderer for tracer frame plans.
//
// Mirrors packages/tracer-spec/python/tracer_spec/render.py: each consecutive pair of plan
// points is a round-capped segment whose width is the mean of the two point widths; the glow
// is the same polyline stroked wider, blurred, and composited underneath at
// GLOW_OPACITY * glow * alpha; the main stroke is drawn opaque into its own layer and
// composited at alpha.
//
// The context types are structural (no DOM lib import) so React Native canvas
// implementations that follow the Canvas 2D API can reuse this.
import { framePlan, GLOW_BLUR_FACTOR, GLOW_OPACITY, GLOW_WIDTH_FACTOR, type FramePlan, type Tracer, type TracerStyle } from "./spec";

export interface Ctx2D {
  globalAlpha: number;
  lineWidth: number;
  lineCap: string;
  lineJoin: string;
  strokeStyle: unknown;
  filter?: string;
  beginPath(): void;
  moveTo(x: number, y: number): void;
  lineTo(x: number, y: number): void;
  stroke(): void;
  clearRect(x: number, y: number, w: number, h: number): void;
  drawImage(image: never, dx: number, dy: number): void;
  save(): void;
  restore(): void;
}

export interface Layer {
  ctx: Ctx2D;
  /** Whatever drawImage accepts for this platform (an OffscreenCanvas or HTMLCanvasElement on web). */
  image: unknown;
}

/** Supplies scratch layers of a given pixel size. Implementations should reuse layers. */
export type LayerFactory = (width: number, height: number, key: "glow" | "main") => Layer;

export interface DrawOptions {
  /** Target pixel size of the destination context. */
  width: number;
  height: number;
  layers: LayerFactory;
}

function strokePolyline(ctx: Ctx2D, plan: FramePlan, w: number, h: number, color: string, scale: number) {
  ctx.strokeStyle = color;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const pts = plan.points;
  for (let k = 0; k < pts.length - 1; k++) {
    const a = pts[k]!;
    const b = pts[k + 1]!;
    ctx.lineWidth = scale * 0.5 * (plan.widths[k]! + plan.widths[k + 1]!) * h;
    ctx.beginPath();
    ctx.moveTo(a[0] * w, a[1] * h);
    ctx.lineTo(b[0] * w, b[1] * h);
    ctx.stroke();
  }
}

/** Draw a frame plan onto ctx (which should already hold the video frame, or be clear for an overlay). */
export function drawPlan(ctx: Ctx2D, plan: FramePlan | null, style: TracerStyle, opts: DrawOptions): void {
  if (plan === null || plan.points.length < 2) return;
  const { width: w, height: h } = opts;
  const glow = style.glow;

  if (glow > 0) {
    const layer = opts.layers(w, h, "glow");
    layer.ctx.clearRect(0, 0, w, h);
    strokePolyline(layer.ctx, plan, w, h, style.color, 1.0 + GLOW_WIDTH_FACTOR * glow);
    const sigma = glow * GLOW_BLUR_FACTOR * style.width * h;
    ctx.save();
    ctx.globalAlpha = GLOW_OPACITY * glow * plan.alpha;
    if (sigma > 0.3) ctx.filter = `blur(${sigma}px)`;
    ctx.drawImage(layer.image as never, 0, 0);
    ctx.restore();
  }

  const main = opts.layers(w, h, "main");
  main.ctx.clearRect(0, 0, w, h);
  strokePolyline(main.ctx, plan, w, h, style.color, 1.0);
  ctx.save();
  ctx.globalAlpha = plan.alpha;
  ctx.drawImage(main.image as never, 0, 0);
  ctx.restore();
}

export function drawTracer(ctx: Ctx2D, tracer: Tracer, t: number, opts: DrawOptions): void {
  drawPlan(ctx, framePlan(tracer, t), tracer.style, opts);
}
