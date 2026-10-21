import { describe, expect, it } from "vitest";

import vectors from "../../vectors/vectors.json";
import { framePlan, headAt, samplePath, validateTracer, type Tracer } from "../src";

const TOL = vectors.tolerance;

function expectClose(actual: unknown, expected: unknown, where: string) {
  if (expected === null || actual === null) {
    expect(actual, where).toBe(expected);
    return;
  }
  if (Array.isArray(expected)) {
    expect(Array.isArray(actual), where).toBe(true);
    const a = actual as unknown[];
    expect(a.length, where).toBe(expected.length);
    expected.forEach((e, i) => expectClose(a[i], e, `${where}[${i}]`));
    return;
  }
  expect(Math.abs((actual as number) - (expected as number)), where).toBeLessThanOrEqual(TOL);
}

describe.each(vectors.cases)("vector case $name", (c) => {
  const tracer = c.tracer as unknown as Tracer;

  it("is valid", () => {
    expect(validateTracer(tracer)).toEqual([]);
  });

  it("samples the full path", () => {
    const full = samplePath(tracer);
    expect(full.length).toBe(c.path_len);
    const every = full.filter((_, i) => i % 25 === 0);
    every.push(full[full.length - 1]!);
    expectClose(every, c.path_every_25, "path");
  });

  it.each(c.frames)("matches head and plan at t=$t", (f) => {
    expectClose(headAt(tracer, f.t), f.head, "head");
    const plan = framePlan(tracer, f.t);
    const exp = f.plan;
    if (exp === null) {
      expect(plan).toBeNull();
      return;
    }
    expect(plan).not.toBeNull();
    expect(plan!.points.length).toBe(exp.n_points);
    expectClose(plan!.alpha, exp.alpha, "alpha");
    expectClose(plan!.head, exp.head, "plan.head");
    expectClose(
      exp.sample_indices.map((i) => plan!.points[i]),
      exp.points,
      "points",
    );
    expectClose(
      exp.sample_indices.map((i) => plan!.widths[i]),
      exp.widths,
      "widths",
    );
  });
});

describe.each(vectors.invalid)("invalid case $name", (c) => {
  it("is rejected", () => {
    expect(validateTracer(c.tracer).length).toBeGreaterThan(0);
  });
});
