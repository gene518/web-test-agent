import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./test-results",
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:1420",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        channel: "chrome",
        viewport: { width: 1280, height: 800 },
      },
    },
  ],
  webServer: {
    command: "pnpm dev --host 127.0.0.1",
    url: "http://127.0.0.1:1420",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
