/**
 * Playwright configuration for the end-to-end smoke test.
 *
 * The smoke test drives the real stack: the Vite dev server and the FastAPI
 * backend with the seeded SQLite database. Both are reused when they are already
 * running (`make dev`), so a developer can run `npx playwright test` against the
 * live app they are looking at.
 */

import { defineConfig, devices } from "@playwright/test"

const FRONTEND = "http://127.0.0.1:5173"
const BACKEND = "http://127.0.0.1:8000"

export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 90_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: FRONTEND,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
    viewport: { width: 1600, height: 900 },
  },
  webServer: [
    {
      command: "npm run dev",
      url: FRONTEND,
      reuseExistingServer: true,
      timeout: 90_000,
    },
    {
      command: "..\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
      cwd: "../backend",
      url: `${BACKEND}/api/health`,
      reuseExistingServer: true,
      timeout: 90_000,
    },
  ],
})
