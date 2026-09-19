import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: "line",
  timeout: 120_000,
  expect: {
    timeout: 15_000,
  },
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8080",
    screenshot: "only-on-failure",
    trace: "off",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: {
        browserName: "chromium",
        channel: process.env.E2E_BROWSER_CHANNEL,
      },
    },
  ],
});
