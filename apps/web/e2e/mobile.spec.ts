// Phone-browser layout: the main screens fit a phone viewport without horizontal scrolling.
import { expect, test } from "@playwright/test";

test("library and tips work on a phone", async ({ page }) => {
  const email = `mobile-${Date.now()}@example.com`;
  await page.goto("/sign-in");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("correct-horse-42");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByRole("button", { name: "Got it" }).click();
  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible();
  await expect(page.getByTestId("drop-zone")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  // Touch targets are at least 44 px.
  const sizes = await page.locator("main button:visible").evaluateAll((els) =>
    els.map((e) => {
      const r = e.getBoundingClientRect();
      return Math.min(r.width, r.height);
    }),
  );
  expect(Math.min(...sizes)).toBeGreaterThanOrEqual(43.5);
  await page.getByRole("link", { name: "Tips", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Recording tips" })).toBeVisible();
});
