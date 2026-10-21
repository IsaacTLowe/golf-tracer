import { clipRejection } from "@golf-tracer/api-client";
import { describe, expect, it } from "vitest";

// The upload hook delegates its rules to the shared package; make sure the web build resolves it.
describe("upload rules wiring", () => {
  it("rejects oversize files with the inline message", () => {
    expect(clipRejection({ name: "a.mp4", type: "video/mp4", size: 600 * 1024 * 1024 })).toBe("Over 500 MB");
  });
});
