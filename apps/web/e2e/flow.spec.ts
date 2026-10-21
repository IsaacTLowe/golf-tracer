// End to end: sign up → bulk upload → processing → review (auto + manual wizard) → edit →
// approve → export → download → delete, against a running stack (E2E_BASE_URL).
import { expect, test, type Page } from "@playwright/test";

import { fixtures } from "./fixtures";

test.describe.configure({ mode: "serial" });

async function signUp(page: Page) {
  const email = `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  await page.goto("/sign-in");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("correct-horse-42");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Before your first upload" })).toBeVisible();
  await page.getByRole("button", { name: "Got it" }).click();
  await expect(page).toHaveURL(/\/library/);
  return email;
}

async function tapFrame(page: Page, x: number, y: number) {
  const box = await page.getByTestId("frame-box").boundingBox();
  if (!box) throw new Error("frame box not visible");
  await page.mouse.click(box.x + box.width * x, box.y + box.height * y);
}

test("full review flow", async ({ page, request }) => {
  const files = fixtures();
  page.on("dialog", (d) => void d.accept());
  await signUp(page);

  // Bulk upload: two valid clips and one rejected inline without blocking the others.
  await page.getByTestId("file-input").setInputFiles([files.auto, files.manual, files.tooShort]);
  await expect(page.locator('[data-upload-status="rejected"]')).toContainText("Shorter than 2 s");
  await expect(page.locator('[data-upload-status="done"]')).toHaveCount(2, { timeout: 60_000 });

  // Library updates live (Realtime) as the worker processes them.
  const grid = page.getByTestId("clip-grid");
  await expect(grid.locator("[data-clip-id]")).toHaveCount(2);
  await expect(grid.locator('[data-status="review"], [data-status="needs_input"]')).toHaveCount(2, { timeout: 150_000 });
  await expect(grid.locator("img")).toHaveCount(2); // thumbnails

  // Review queue: oldest first. Handle whichever state each clip is in.
  await page.getByTestId("review-queue").click();
  for (let i = 0; i < 2; i++) {
    await expect(page).toHaveURL(/\/clips\/[0-9a-f-]+\?queue=1/);
    const wizard = page.getByTestId("wizard");
    await expect(page.getByTestId("actions")).toBeVisible();
    await expect(page.locator("video")).toBeVisible();
    const needsInput = (await page.locator("main [data-status]").first().getAttribute("data-status")) === "needs_input";
    if (needsInput) {
      await expect(wizard).toBeVisible();
      // Three taps: ball at impact, apex, landing. The video is pre-set to the detected impact.
      await tapFrame(page, 0.5, 0.83);
      await expect(wizard).toContainText("Step 2 of 3");
      await tapFrame(page, 0.52, 0.34);
      await expect(wizard).toContainText("Step 3 of 3");
      await tapFrame(page, 0.53, 0.53);
      // Lands in edit mode with the new tracer; nudge the apex and save a new version.
      const panel = page.getByTestId("edit-panel");
      await expect(panel).toBeVisible();
      await expect(panel).toContainText("v1");
      const apex = page.locator('[data-handle="apex"]');
      const hb = (await apex.boundingBox())!;
      await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2);
      await page.mouse.down();
      await page.mouse.move(hb.x + hb.width / 2 + 25, hb.y + hb.height / 2 - 15, { steps: 5 });
      await page.mouse.up();
      await expect(panel).toContainText("unsaved");
      await page.keyboard.press("Control+z");
      await expect(panel).not.toContainText("unsaved");
      await page.keyboard.press("Control+Shift+z");
      await expect(panel).toContainText("unsaved");
      await page.getByTestId("save-tracer").click();
      await expect(panel).toBeHidden();
      await expect(page.getByTestId("approve")).toBeEnabled();
      await page.getByTestId("approve").click();
    } else {
      // Auto tracer: play a moment, check the edit panel opens, then approve with the keyboard.
      await page.getByTestId("edit").click();
      await expect(page.getByTestId("edit-panel")).toBeVisible();
      await page.getByRole("button", { name: "Cancel" }).click();
      await page.locator("body").press("a");
    }
    if (i === 0) await expect(page).toHaveURL(/\?queue=1/);
  }
  await expect(page).toHaveURL(/\/library/);
  await expect(grid.locator('[data-status="approved"]')).toHaveCount(2, { timeout: 30_000 });

  // Export one clip and download it.
  await grid.locator("[data-clip-id]").first().click();
  await page.getByTestId("export").click();
  const download = page.getByTestId("download");
  await expect(download).toBeVisible({ timeout: 120_000 });
  const href = await download.getAttribute("href");
  const res = await request.get(href!);
  expect(res.status()).toBe(200);
  expect(res.headers()["content-type"]).toContain("video/mp4");
  expect((await res.body()).length).toBeGreaterThan(20_000);

  // Delete it.
  await page.getByRole("button", { name: "Delete" }).click();
  await expect(page).toHaveURL(/\/library/);
  await expect(grid.locator("[data-clip-id]")).toHaveCount(1);
});
