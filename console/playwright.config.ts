import { defineConfig } from "@playwright/test";

// The console under test runs on CONSOLE_URL (default http://127.0.0.1:3000) against the
// orchestrator API served by tests/e2e/serve_seeded_api.py on ORCHESTRATOR_URL; `npm run e2e`
// expects both up with the same AUTH_SECRET, CONSOLE_SESSION_KEY and CONSOLE_DB_PATH, which the
// global setup uses to mint a signed-in session (see context/specs/59-overview.md). The
// controls project runs last: its kill-switch cancels the seeded running task.
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 90_000,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.CONSOLE_URL ?? "http://127.0.0.1:3000",
    storageState: "e2e/.auth/state.json",
    viewport: { width: 1440, height: 900 },
    colorScheme: "light",
  },
  projects: [
    { name: "screens", use: { browserName: "chromium" }, testIgnore: /controls\.spec\.ts/ },
    { name: "controls", use: { browserName: "chromium" }, testMatch: /controls\.spec\.ts/, dependencies: ["screens"] },
  ],
});
