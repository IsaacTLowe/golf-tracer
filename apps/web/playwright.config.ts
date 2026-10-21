import { defineConfig, devices } from "@playwright/test";

// Two projects:
//   parity  – renderer parity check; needs only Python (uv) and a browser.
//   e2e     – full flow against a running stack (web, API, worker, Supabase);
//             set E2E_BASE_URL (default http://localhost:3000).
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    { name: "parity", testMatch: /parity\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    { name: "e2e", testMatch: /flow\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    { name: "e2e-mobile", testMatch: /mobile\.spec\.ts/, use: { ...devices["Pixel 7"] } },
  ],
});
