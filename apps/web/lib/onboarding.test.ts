import { beforeEach, describe, expect, it } from "vitest";

import { afterSignIn, markOnboarded } from "./onboarding";

describe("afterSignIn", () => {
  beforeEach(() => localStorage.clear());

  it("shows the recording tips once, then goes where the user was headed", () => {
    expect(afterSignIn("/clips/abc")).toBe("/tips?onboarding=1&next=%2Fclips%2Fabc");
    markOnboarded();
    expect(afterSignIn("/clips/abc")).toBe("/clips/abc");
  });

  it("ignores off-site redirect targets", () => {
    markOnboarded();
    expect(afterSignIn("//evil.example")).toBe("/library");
    expect(afterSignIn("https://evil.example")).toBe("/library");
    expect(afterSignIn(null)).toBe("/library");
  });
});
