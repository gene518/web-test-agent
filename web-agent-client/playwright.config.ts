import { defineConfig, devices } from "@playwright/test";

const devServerPort = 1420;
const devServerUrl = `http://127.0.0.1:${devServerPort}`;

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./test-results",
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  reporter: "line",
  use: {
    baseURL: devServerUrl,
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
    command: `pnpm dev --host 127.0.0.1 --port ${devServerPort}`,
    url: devServerUrl,
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
