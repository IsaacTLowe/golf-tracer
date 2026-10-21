// Tracer spec v1: curve, timing and drawing-plan math.
//
// TypeScript twin of packages/tracer-spec/python/tracer_spec/spec.py. Both must produce
// identical numbers (to 1e-6) for the shared vectors in packages/tracer-spec/vectors.
// Change them together. No DOM or framework imports: this package is shared with React Native.

export const SCHEMA_VERSION = 1;
export const SAMPLES_PER_SEGMENT = 200;
export const FADE_S = 0.3;
export const GLOW_WIDTH_FACTOR = 2.0;
export const GLOW_BLUR_FACTOR = 0.75;
export const GLOW_OPACITY = 0.6;
export const EPS = 1e-6;

export type Point = [number, number];

export interface TracerVideo {
  width: number;
  height: number;
  fps: number;
  duration_s: number;
}

export interface TracerPath {
  start: Point;
  c1: Point;
  apex: Point;
  c2: Point;
  landing: Point;
}

export interface TracerTiming {
  ascent_ease: number;
  descent_ease: number;
}

export interface TracerStyle {
  color: string;
  width: number;
  taper: number;
  glow: number;
  persist_s: number | null;
}

export interface ObservedPoint {
  t: number;
  x: number;
  y: number;
  score?: number;
}

export interface TracerFit {
  method?: string;
  residual_px?: number | null;
  confidence?: number | null;
}

export interface Tracer {
  schema_version: 1;
  video: TracerVideo;
  impact_time_s: number;
  apex_time_s: number;
  landing_time_s: number;
  path: TracerPath;
  timing: TracerTiming;
  style: TracerStyle;
  observed?: ObservedPoint[];
  fit?: TracerFit;
}

export interface FramePlan {
  points: Point[];
  /** Stroke width at each point, as a fraction of frame height. */
  widths: number[];
  alpha: number;
  /** Current ball position during flight; null after landing. */
  head: Point | null;
}

export const DEFAULT_STYLE: TracerStyle = {
  color: "#FF3B30",
  width: 0.006,
  taper: 0.4,
  glow: 0.5,
  persist_s: 2.0,
};

export const DEFAULT_TIMING: TracerTiming = { ascent_ease: 2.0, descent_ease: 1.3 };

export function bezier(u: number, p0: Point, c: Point, p1: Point): Point {
  const a = (1.0 - u) * (1.0 - u);
  const b = 2.0 * (1.0 - u) * u;
  const d = u * u;
  return [a * p0[0] + b * c[0] + d * p1[0], a * p0[1] + b * c[1] + d * p1[1]];
}

export function sampleU(i: number): number {
  return i / (SAMPLES_PER_SEGMENT - 1);
}

/** [segment, u] of the ball head at time t (0 = ascent, 1 = descent); null before impact. */
export function headParam(tracer: Tracer, t: number): [0 | 1, number] | null {
  const ti = tracer.impact_time_s;
  const ta = tracer.apex_time_s;
  const tl = tracer.landing_time_s;
  if (t < ti) return null;
  if (t <= ta) {
    const tau = (t - ti) / (ta - ti);
    return [0, 1.0 - Math.pow(1.0 - tau, tracer.timing.ascent_ease)];
  }
  if (t <= tl) {
    const tau = (t - ta) / (tl - ta);
    return [1, Math.pow(tau, tracer.timing.descent_ease)];
  }
  return [1, 1.0];
}

export function headAt(tracer: Tracer, t: number): Point | null {
  const hp = headParam(tracer, t);
  if (hp === null) return null;
  const [seg, u] = hp;
  const p = tracer.path;
  return seg === 0 ? bezier(u, p.start, p.c1, p.apex) : bezier(u, p.apex, p.c2, p.landing);
}

/** The full path: 200 ascent samples then descent samples 1..199 (apex not repeated). */
export function samplePath(tracer: Tracer): Point[] {
  const p = tracer.path;
  const pts: Point[] = [];
  for (let i = 0; i < SAMPLES_PER_SEGMENT; i++) pts.push(bezier(sampleU(i), p.start, p.c1, p.apex));
  for (let i = 1; i < SAMPLES_PER_SEGMENT; i++) pts.push(bezier(sampleU(i), p.apex, p.c2, p.landing));
  return pts;
}

function pxDist(a: Point, b: Point, w: number, h: number): number {
  return Math.hypot((b[0] - a[0]) * w, (b[1] - a[1]) * h);
}

function cumulative(points: Point[], w: number, h: number): number[] {
  const cum = [0.0];
  for (let k = 1; k < points.length; k++) cum.push(cum[k - 1]! + pxDist(points[k - 1]!, points[k]!, w, h));
  return cum;
}

/** Overall opacity at t (assumes t >= impact). 0 means draw nothing. */
export function fadeAlpha(tracer: Tracer, t: number): number {
  const tl = tracer.landing_time_s;
  if (t <= tl) return 1.0;
  const persist = tracer.style.persist_s;
  if (persist === null) return 1.0;
  const dt = t - tl - persist;
  if (dt <= 0) return 1.0;
  if (dt < FADE_S) return 1.0 - dt / FADE_S;
  return 0.0;
}

export function widthAt(style: TracerStyle, s: number, total: number): number {
  const frac = total > 0 ? s / total : 0.0;
  return style.width * (1.0 - (1.0 - style.taper) * frac);
}

/** Everything a renderer needs at video time t, or null when nothing is drawn. */
export function framePlan(tracer: Tracer, t: number): FramePlan | null {
  const hp = headParam(tracer, t);
  if (hp === null) return null;
  const alpha = fadeAlpha(tracer, t);
  if (alpha <= 0.0) return null;
  const w = tracer.video.width;
  const h = tracer.video.height;
  const full = samplePath(tracer);
  const cum = cumulative(full, w, h);
  const total = cum[cum.length - 1]!;

  let points: Point[];
  let arcs: number[];
  let head: Point | null;
  if (t > tracer.landing_time_s) {
    points = full;
    arcs = cum;
    head = null;
  } else {
    const [seg, uh] = hp;
    let n = 0;
    if (seg === 0) {
      for (let i = 0; i < SAMPLES_PER_SEGMENT; i++) if (sampleU(i) < uh) n++;
    } else {
      n = SAMPLES_PER_SEGMENT;
      for (let i = 1; i < SAMPLES_PER_SEGMENT; i++) if (sampleU(i) < uh) n++;
    }
    head = headAt(tracer, t)!;
    points = [...full.slice(0, n), head];
    arcs = cum.slice(0, n);
    const last = arcs.length ? arcs[arcs.length - 1]! : 0.0;
    arcs.push(last + (n ? pxDist(full[n - 1]!, head, w, h) : 0.0));
  }
  const widths = arcs.map((s) => widthAt(tracer.style, s, total));
  return { points: points.map((p) => [p[0], p[1]] as Point), widths, alpha, head };
}
