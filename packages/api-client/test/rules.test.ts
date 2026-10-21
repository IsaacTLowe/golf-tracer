import { describe, expect, it } from "vitest";

import { clipContentType, clipRejection } from "../src/rules";

const ok = { name: "swing.mp4", type: "video/mp4", size: 10_000_000, durationS: 8, width: 1080, height: 1920 };

describe("clipRejection", () => {
  it("accepts a normal clip", () => {
    expect(clipRejection(ok)).toBeNull();
  });
  it.each([
    [{ size: 501 * 1024 * 1024 }, "Over 500 MB"],
    [{ durationS: 61 }, "Longer than 60 s"],
    [{ durationS: 1.2 }, "Shorter than 2 s"],
    [{ width: 640, height: 360 }, "Below 480p"],
    [{ name: "swing.avi", type: "video/x-msvideo" }, "Not an MP4, MOV or WebM video"],
    [{ size: 0 }, "Empty file"],
  ])("rejects %o", (patch, reason) => {
    expect(clipRejection({ ...ok, ...patch })).toBe(reason);
  });
  it("skips the duration check when the browser couldn't decode (HEVC)", () => {
    expect(clipRejection({ ...ok, name: "IMG_1.MOV", type: "", durationS: null, width: null, height: null })).toBeNull();
  });
});

describe("clipContentType", () => {
  it("fills in missing types from the extension", () => {
    expect(clipContentType({ name: "a.MOV", type: "" })).toBe("video/quicktime");
    expect(clipContentType({ name: "a.webm", type: "" })).toBe("video/webm");
    expect(clipContentType({ name: "a.mp4", type: "video/mp4" })).toBe("video/mp4");
  });
});
