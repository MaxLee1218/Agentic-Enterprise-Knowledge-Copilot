import { defineConfig } from "@playwright/test";

const python = process.env.PLAYWRIGHT_PYTHON ?? "python";
const viteCommand =
  process.env.PLAYWRIGHT_VITE_COMMAND ??
  "npm run dev -- --host 127.0.0.1 --port 4173";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 45_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: process.env.PLAYWRIGHT_EXTERNAL_SERVER
    ? undefined
    : [
        {
          command: `${python} -m uvicorn tests.frontend_e2e_app:app --app-dir .. --host 127.0.0.1 --port 8000`,
          url: "http://127.0.0.1:8000/health",
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
        },
        {
          command: viteCommand,
          url: "http://127.0.0.1:4173",
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
        },
      ],
  projects: [
    {
      name: "chromium",
      use:
        process.env.PLAYWRIGHT_CHANNEL === "chrome"
          ? { browserName: "chromium", channel: "chrome" }
          : { browserName: "chromium" },
    },
  ],
});
