import { describe, expect, it } from "vitest";

import vectors from "../../vectors/vectors.json";
import { moveHandle, moveTime, setStyle, shiftToImpact, validateTracer, withoutDiagnostics, type Tracer } from "../src";

const base = vectors.cases[0]!.tracer as unknown as Tracer;

describe("moveHandle", () => {
  it("moves c1 and c2 with the apex and keeps their y equal", () => {
    const t = moveHandle(base, "apex", [0.6, 0.2]);
    expect(t.path.apex).toEqual([0.6, 0.2]);
    expect(t.path.c1[1]).toBe(0.2);
    expect(t.path.c2[1]).toBe(0.2);
    expect(t.path.c1[0]).toBeCloseTo(base.path.c1[0] + (0.6 - base.path.apex[0]), 12);
    expect(validateTracer(t)).toEqual([]);
  });

  it("never lets the apex drop below start or landing", () => {
    const t = moveHandle(base, "apex", [0.5, 0.95]);
    expect(t.path.apex[1]).toBeLessThanOrEqual(t.path.landing[1]);
    expect(validateTracer(t)).toEqual([]);
  });

  it("pins advanced handles to the apex line", () => {
    const t = moveHandle(base, "c1", [0.3, 0.9]);
    expect(t.path.c1).toEqual([0.3, base.path.apex[1]]);
    expect(validateTracer(t)).toEqual([]);
  });

  it("clamps to the frame", () => {
    const t = moveHandle(base, "landing", [1.4, -2]);
    expect(t.path.landing[0]).toBe(1);
    expect(validateTracer(t)).toEqual([]);
  });

  it("does not mutate its input", () => {
    const before = JSON.stringify(base);
    moveHandle(base, "start", [0.1, 0.9]);
    expect(JSON.stringify(base)).toBe(before);
  });
});

describe("moveTime", () => {
  it("keeps impact < apex < landing <= duration", () => {
    let t = moveTime(base, "apex", 100);
    expect(t.apex_time_s).toBeLessThan(t.landing_time_s);
    t = moveTime(t, "landing", 100);
    expect(t.landing_time_s).toBe(base.video.duration_s);
    t = moveTime(t, "impact", 99);
    expect(t.impact_time_s).toBeLessThan(t.apex_time_s);
    expect(validateTracer(t)).toEqual([]);
  });
});

describe("shiftToImpact", () => {
  it("keeps flight timing proportions", () => {
    const t = shiftToImpact(base, 1.0);
    expect(t.impact_time_s).toBe(1.0);
    expect(t.landing_time_s - t.impact_time_s).toBeCloseTo(base.landing_time_s - base.impact_time_s, 9);
    expect(validateTracer(t)).toEqual([]);
  });
});

describe("style and diagnostics", () => {
  it("merges style and strips diagnostics", () => {
    const t = withoutDiagnostics(setStyle(base, { color: "#FFFFFF" }));
    expect(t.style.color).toBe("#FFFFFF");
    expect(t.style.width).toBe(base.style.width);
    expect(t.observed).toBeUndefined();
    expect(t.fit).toBeUndefined();
  });
});
