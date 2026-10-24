import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./practice-e2e",
  workers: 1,
  timeout: 180000,
  expect: { timeout: 15000 },
  reporter: [
    ["list"],
    ["json", { outputFile: "../../var/p3b/real-browser-results.json" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:8783",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    launchOptions: process.env.PTCG_BROWSER_PATH
      ? { executablePath: process.env.PTCG_BROWSER_PATH }
      : {},
  },
  webServer: {
    command: "node ../../scripts/migration/p3b_server.mjs",
    url: "http://127.0.0.1:8783/api/meta",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
