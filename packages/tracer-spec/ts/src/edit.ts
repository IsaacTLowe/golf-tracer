// Pure editing operations used by the tracer editor (web now, React Native later).
// Every operation returns a new tracer that still satisfies the cross-field rules.
import { EPS, type Point, type Tracer, type TracerStyle } from "./spec";

export type Handle = "start" | "apex" | "landing" | "c1" | "c2";
export type TimeMarker = "impact" | "apex" | "landing";

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

function clone(t: Tracer): Tracer {
  return JSON.parse(JSON.stringify(t)) as Tracer;
}

/** Move one path handle to a normalized point, keeping c1/apex/c2 on one y and the apex highest. */
export function moveHandle(tracer: Tracer, handle: Handle, to: Point): Tracer {
  const t = clone(tracer);
  const p = t.path;
  const x = clamp01(to[0]);
  const y = clamp01(to[1]);
  switch (handle) {
    case "start":
      p.start = [x, Math.max(y, p.apex[1])];
      break;
    case "landing":
      p.landing = [x, Math.max(y, p.apex[1])];
      break;
    case "apex": {
      const ay = Math.min(y, p.start[1], p.landing[1]);
      const dx = x - p.apex[0];
      p.apex = [x, ay];
      p.c1 = [clamp01(p.c1[0] + dx), ay];
      p.c2 = [clamp01(p.c2[0] + dx), ay];
      break;
    }
    case "c1":
      p.c1 = [x, p.apex[1]];
      break;
    case "c2":
      p.c2 = [x, p.apex[1]];
      break;
  }
  return t;
}

/** Move a timeline marker, keeping impact < apex < landing <= duration with at least one frame between. */
export function moveTime(tracer: Tracer, marker: TimeMarker, seconds: number): Tracer {
  const t = clone(tracer);
  const gap = Math.max(1 / t.video.fps, 0.001);
  const dur = t.video.duration_s;
  if (marker === "impact") {
    t.impact_time_s = Math.min(Math.max(0, seconds), t.apex_time_s - gap);
  } else if (marker === "apex") {
    t.apex_time_s = Math.min(Math.max(seconds, t.impact_time_s + gap), t.landing_time_s - gap);
  } else {
    t.landing_time_s = Math.min(Math.max(seconds, t.apex_time_s + gap), dur);
  }
  return t;
}

/** Shift the whole tracer in time so impact lands on `seconds` (used when re-picking impact). */
export function shiftToImpact(tracer: Tracer, seconds: number): Tracer {
  const t = clone(tracer);
  const flight = t.landing_time_s - t.impact_time_s;
  const apexFrac = (t.apex_time_s - t.impact_time_s) / flight;
  const impact = Math.max(0, Math.min(seconds, t.video.duration_s - 2 * EPS - 0.01));
  const landing = Math.min(impact + flight, t.video.duration_s);
  t.impact_time_s = impact;
  t.landing_time_s = landing;
  t.apex_time_s = impact + (landing - impact) * apexFrac;
  return t;
}

export function setStyle(tracer: Tracer, style: Partial<TracerStyle>): Tracer {
  const t = clone(tracer);
  t.style = { ...t.style, ...style };
  return t;
}

/** Strip diagnostic fields before saving an edited version. */
export function withoutDiagnostics(tracer: Tracer): Tracer {
  const t = clone(tracer);
  delete t.observed;
  delete t.fit;
  return t;
}
